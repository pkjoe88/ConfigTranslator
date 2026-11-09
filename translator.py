# -*- coding: utf-8 -*-
"""
php.ini & httpd.conf 英文註解全自動中文化工具 (V6)

翻譯引擎（阿達 2026-10-24 定案）：
  主引擎 Gemini —— 譯文要是「繁體中文、台灣口語」，只有 LLM 能照 prompt 控制口吻；
                    機制比照 changdoctopdf/payout_ocr.py 的既有慣例（見下）。
  備援 Google gtx —— Gemini 沒金鑰、額度用盡或連不上時頂上，至少把意思翻出來
                    （口吻是固定的書面腔，這是它先天的限制）。

Gemini 機制（與 payout_ocr.py 相同慣例，方便共用同一組金鑰）：
  * 金鑰來源：環境變數 GEMINI_API_KEYS（逗號／分號分隔，每把要是不同
    Google Cloud 專案）優先，其次 GEMINI_API_KEY。用 changdoctopdf 的
    set_gemini_keys.ps1 設好的金鑰，這支可以直接共用，不用再設一次。
  * 免費額度是「每專案、每模型」計算：429 回「retry in 14h」類訊息＝今日
    額度用盡 → 立刻把那把標死、換下一把，不做無謂退避；短暫 429/503 才退避。
  * thinkingBudget 必關：gemini-3.x-flash 是 thinking 模型，不關會把
    maxOutputTokens 被思考吃光、答案截斷。
  * 批次翻譯：免費額度以「次數」計，逐段打必爆。把多段編號後一次送出
    （預設 12 段／次），回來再依編號拆回；整份 php.ini 約 25 次呼叫內完成。

其他 V5 已修、V6 沿用：
  * gtx 端點網址與回應解析改對（V4 的網址回 404、解析抓不到譯文）。
  * 連續註解先合併成段；被註解掉的參數行（;extension=...）、段落標頭不送翻。
  * 翻譯快取落盤（translation_cache.json），重跑可接續；失敗列行號，不靜默。
  * 編碼讀取先讀 bytes 再嚴格解碼，不再用 errors='ignore' 吞字元。

僅使用 Python 標準函式庫。
"""
import os
import re
import sys
import json
import time
import textwrap
import urllib.parse
import urllib.request
import urllib.error

# ============================== 共用設定 ==============================

CACHE_FILE = "translation_cache.json"

# ============================== Gemini 引擎 ==============================

GEMINI_MODEL = os.environ.get('TRANSLATE_GEMINI_MODEL', 'gemini-3.6-flash')
GEMINI_URL = ('https://generativelanguage.googleapis.com/v1beta/models/'
              + GEMINI_MODEL + ':generateContent')
GEMINI_BATCH_SIZE = 12        # 每次呼叫打包幾段（免費額度以次數計，要省著用）
GEMINI_PACE_SEC = 5.0         # 每次呼叫之間的最小間隔，避免短時間連打被 503
GEMINI_TIMEOUT = 60

_BATCH_PROMPT = """你是翻譯員。把下面編號的英文段落逐段翻成繁體中文。

口吻要求（最重要）：
- 用台灣人平常說話的口語、白話講這段話的意思，不要書面直譯腔。
- 像在跟同事解釋「這個設定是幹嘛的」，自然就好，不要為了口語硬加語助詞。

格式規則（錯了整批作廢，務必遵守）：
- 每段譯文以 [[編號]] 開頭，編號與輸入完全對應；不要合併段落、不要漏段。
- 只輸出譯文，不要加說明、標題、引號或任何額外文字。
- 參數名、指令名、檔名、URL、數字與單位、程式碼片段（如 directive = value）一律保留原文，不要翻譯。

以下是原文段落：
"""

_SEGMENT_HEAD_RE = re.compile(r'\[\[(\d+)\]\]')


def gemini_keys():
    """可用金鑰池（與 payout_ocr._keys() 相同規則）：
    GEMINI_API_KEYS（逗號／分號分隔）優先，GEMINI_API_KEY 作後備。"""
    raw = os.environ.get('GEMINI_API_KEYS', '')
    keys = [k.strip() for k in re.split(r'[,;]', raw) if k.strip()]
    one = os.environ.get('GEMINI_API_KEY', '').strip()
    if one and one not in keys:
        keys.append(one)
    return keys


class GeminiEngine:
    """批次翻譯引擎。translate_batch(texts) 回傳與輸入等長的 list，
    單段失敗的位置是 None；全部金鑰都不可用時整批 None。"""

    def __init__(self):
        self.keys = gemini_keys()
        self._dead_until = {}   # key -> 今日額度用盡的解鎖時間戳
        self._last_call_at = 0.0
        self.calls = 0

    @property
    def available(self):
        return bool(self.keys)

    def _live_keys(self):
        now = time.time()
        return [k for k in self.keys if self._dead_until.get(k, 0.0) <= now]

    def _pace(self):
        wait = GEMINI_PACE_SEC - (time.time() - self._last_call_at)
        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def _parse_batch_reply(reply_text, expected):
        """把 [[1]] ... [[2]] ... 格式的回覆拆回段落；編號缺漏就回傳 None 交給上層逐段處理。"""
        parts = _SEGMENT_HEAD_RE.split(reply_text)
        # split 結果：[前置文字, '1', 內容1, '2', 內容2, ...]
        found = {}
        it = iter(parts[1:])
        for num, body in zip(it, it):
            found[int(num)] = body.strip()
        if sorted(found) != list(range(1, expected + 1)):
            return None
        if any(not found[i] for i in found):
            return None
        return [found[i] for i in range(1, expected + 1)]

    def _call_once(self, key, texts):
        joined = "\n\n".join(f"[[{i + 1}]]\n{t}" for i, t in enumerate(texts))
        body = {
            'contents': [{'parts': [{'text': _BATCH_PROMPT + joined}]}],
            # thinkingBudget 必關（比照 payout_ocr 的實測結論）
            'generationConfig': {
                'temperature': 0,
                'maxOutputTokens': 8192,
                'thinkingConfig': {'thinkingBudget': 0},
            },
        }
        req = urllib.request.Request(
            GEMINI_URL + '?key=' + key,
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as r:
            resp = json.load(r)
        cand = (resp.get('candidates') or [{}])[0]
        parts = ((cand.get('content') or {}).get('parts')) or []
        reply = "".join(p.get('text', '') for p in parts).strip()
        return reply or None

    def translate_batch(self, texts):
        if not texts:
            return []
        for key in self._live_keys():
            waits_503 = (15, 45)
            for attempt in range(3):
                self._pace()
                try:
                    reply = self._call_once(key, texts)
                    self._last_call_at = time.time()
                    self.calls += 1
                    if reply is None:
                        break  # 回空的，換下一把
                    parsed = self._parse_batch_reply(reply, len(texts))
                    if parsed is not None:
                        return parsed
                    # 格式跑掉：這批改由呼叫端逐段處理（回傳全 None 前先試單段太貴，
                    # 直接整批交給備援／逐段邏輯決定）
                    return [None] * len(texts)
                except urllib.error.HTTPError as e:
                    self._last_call_at = time.time()
                    eb = ""
                    try:
                        eb = e.read().decode('utf-8', errors='ignore')
                    except Exception:
                        pass
                    is_429 = (e.code == 429 or 'RESOURCE_EXHAUSTED' in eb)
                    is_503 = (e.code == 503 or 'UNAVAILABLE' in eb)
                    if is_429:
                        # 看回覆有沒有給 retry 時間：小時級＝今日額度用盡，標死換鑰；
                        # 分鐘級＝短暫限流，睡一下再試同一把。
                        m = re.search(r'retry in (\d+(?:\.\d+)?)(h|m|s)', eb)
                        long_quota = False
                        if m:
                            val, unit = float(m.group(1)), m.group(2)
                            secs = val * {'h': 3600, 'm': 60, 's': 1}[unit]
                            if secs >= 3600:
                                long_quota = True
                                self._dead_until[key] = time.time() + secs
                        if long_quota or not m:
                            # 沒給時間也先當額度問題處理：換鑰比原地等更有效率
                            if not m:
                                self._dead_until[key] = time.time() + 3600
                            break
                        time.sleep(min(secs, 120))
                        continue
                    if is_503 and attempt < len(waits_503):
                        time.sleep(waits_503[attempt])
                        continue
                    break  # 其他 HTTP 錯誤：換下一把
                except Exception:
                    self._last_call_at = time.time()
                    break  # 連線類錯誤：換下一把
        return [None] * len(texts)

    def translate_batched(self, texts):
        """把大量段落切片成批送出；回傳等長 list（失敗處 None）。"""
        results = [None] * len(texts)
        for start in range(0, len(texts), GEMINI_BATCH_SIZE):
            chunk = texts[start:start + GEMINI_BATCH_SIZE]
            got = self.translate_batch(chunk)
            for i, val in enumerate(got):
                results[start + i] = val
        return results


# ============================== gtx 備援引擎 ==============================

GTX_URL = "https://translate.googleapis.com/translate_a/single"
GTX_INTERVAL = 0.15
GTX_RETRIES = 3


def parse_gtx_response(res_json):
    """gtx 回應：res[0] = [[譯文片段, 原文片段, ...], ...]，片段拼起來即譯文。"""
    if not isinstance(res_json, list) or not res_json:
        return None
    segments = res_json[0]
    if not isinstance(segments, list):
        return None
    pieces = [seg[0] for seg in segments
              if isinstance(seg, list) and seg and isinstance(seg[0], str)]
    return "".join(pieces) if pieces else None


class GtxEngine:
    _last_call_at = 0.0

    def translate(self, text):
        params = {"client": "gtx", "sl": "en", "tl": "zh-TW", "dt": "t", "q": text}
        url = GTX_URL + "?" + urllib.parse.urlencode(params)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                                 'AppleWebKit/537.36 (KHTML, like Gecko) '
                                 'Chrome/122.0.0.0 Safari/537.36'}
        for attempt in range(GTX_RETRIES):
            wait = GTX_INTERVAL - (time.time() - GtxEngine._last_call_at)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers=headers, method='GET')
                with urllib.request.urlopen(req, timeout=15) as response:
                    res_json = json.loads(response.read().decode('utf-8'))
                GtxEngine._last_call_at = time.time()
                translated = parse_gtx_response(res_json)
                if translated is not None:
                    return translated
            except Exception:
                GtxEngine._last_call_at = time.time()
            if attempt < GTX_RETRIES - 1:
                time.sleep(2 ** attempt)
        return None


# ============================== 翻譯調度（快取＋主／備援） ==============================

class Translator:
    def __init__(self, cache_path=CACHE_FILE):
        self.cache_path = cache_path
        self.cache = {}
        self.stats = {"gemini": 0, "gtx": 0, "cache_hits": 0, "failed": 0}
        self.gemini = GeminiEngine()
        self.gtx = GtxEngine()
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.cache = loaded
            except (OSError, ValueError):
                pass

    def save_cache(self):
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=0)
        except OSError:
            pass

    def translate_all(self, texts):
        """一次翻完整批段落：先查快取，其餘 Gemini 批次 → 失敗的交 gtx 逐段 →
        再失敗回 None（呼叫端保留原文並列入失敗清單）。"""
        results = [None] * len(texts)
        pending_idx, pending_txt = [], []
        for i, t in enumerate(texts):
            if t in self.cache:
                results[i] = self.cache[t]
                self.stats["cache_hits"] += 1
            else:
                pending_idx.append(i)
                pending_txt.append(t)

        if pending_txt and self.gemini.available:
            got = self.gemini.translate_batched(pending_txt)
            still = []
            for j, val in enumerate(got):
                if val is not None:
                    results[pending_idx[j]] = val
                    self.cache[pending_txt[j]] = val
                    self.stats["gemini"] += 1
                else:
                    still.append(j)
            pending_idx = [pending_idx[j] for j in still]
            pending_txt = [pending_txt[j] for j in still]
        elif pending_txt and not self.gemini.available:
            print("ℹ️ 找不到 Gemini 金鑰（GEMINI_API_KEYS / GEMINI_API_KEY），"
                  "全部改用 gtx 備援引擎（書面腔）。")

        for i, t in zip(pending_idx, pending_txt):
            val = self.gtx.translate(t)
            if val is not None:
                results[i] = val
                self.cache[t] = val
                self.stats["gtx"] += 1
            else:
                self.stats["failed"] += 1
        return results


# ============================== 設定檔處理 ==============================

_DIRECTIVE_RE = re.compile(r'^[A-Za-z_][\w.\-]*\s*=')
_SECTION_RE = re.compile(r'^\[.*\]$')


def is_translatable_comment(content):
    """該不該送翻：要含英文字母、夠長；排除被註解掉的參數行、段落標頭、<...> 標籤行。"""
    if not content or len(content) <= 2:
        return False
    if not any(c.isalpha() for c in content):
        return False
    if _DIRECTIVE_RE.match(content):
        return False
    if _SECTION_RE.match(content):
        return False
    if content.startswith('<'):
        return False
    return True


def read_lines(path):
    """先讀 bytes 再逐一嚴格解碼；全部失敗才用 replace 並回報不乾淨。"""
    with open(path, 'rb') as f:
        raw = f.read()
    for encoding in ('utf-8-sig', 'utf-8', 'big5', 'gbk', 'cp950', 'cp1252'):
        try:
            return raw.decode(encoding).splitlines(keepends=True), encoding, True
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace').splitlines(keepends=True), 'utf-8(replace)', False


def translate_config_file(input_file, output_file, comment_char=';'):
    if not os.path.exists(input_file):
        print(f"❌ 錯誤：找不到檔案 '{input_file}'，請確認檔案放置路徑。")
        return

    lines, encoding, clean = read_lines(input_file)
    if not clean:
        print("⚠️ 警告：無法完整解碼檔案編碼，已用替代字元讀入，輸出可能有缺字。")
    total_lines = len(lines)
    print(f"📂 成功讀取原始檔案 '{input_file}'（編碼：{encoding}），總計 {total_lines} 行。")

    # ---- 第一遍：掃描，把輸出拆成「定稿行」與「待翻段落」 ----
    # plan 元素：('line', 文字) 原樣輸出；('block', seg_idx, indent) 註解段，組裝時折行；
    #             ('inline', seg_idx, config_part) 設定行＋行尾註解。
    plan = []
    segments = []       # 待翻文字
    seg_meta = []       # (kind, line_no) 對應 segments，kind: 'block' / 'inline'
    block = []          # 累積中的連續註解段：(line_no, indent, content)

    def flush_block():
        if not block:
            return
        merged = " ".join(content for _, _, content in block)
        originals = [(ind, content) for _, ind, content in block]
        idx = len(segments)
        segments.append(merged)
        seg_meta.append(('block', block[0][0]))
        plan.append(('block', idx, block[0][1], originals))
        block.clear()

    for idx_line, line in enumerate(lines, 1):
        stripped = line.strip()
        newline = line.rstrip('\r\n')
        text_line = line if line.endswith('\n') else line + '\n'

        if stripped.startswith(comment_char):
            content = stripped.lstrip(comment_char).strip()
            if is_translatable_comment(content):
                indent = newline[:newline.find(comment_char)]
                block.append((idx_line, indent, content))
            else:
                flush_block()
                plan.append(('line', text_line))
        elif comment_char in newline and stripped:
            flush_block()
            config_part, comment_part = newline.split(comment_char, 1)
            comment_text = comment_part.strip()
            if is_translatable_comment(comment_text):
                idx = len(segments)
                segments.append(comment_text)
                seg_meta.append(('inline', idx_line))
                plan.append(('inline', idx, config_part))
            else:
                plan.append(('line', text_line))
        else:
            flush_block()
            plan.append(('line', text_line))
    flush_block()

    print(f"🔍 共 {len(segments)} 段註解要翻，開始翻譯"
          f"（Gemini 主引擎{'有金鑰' if GeminiEngine().available else '無金鑰'}，批次 {GEMINI_BATCH_SIZE} 段／次）...")

    # ---- 第二遍：整批翻譯 ----
    translator = Translator()
    translated = translator.translate_all(segments)
    translator.save_cache()

    failures = [(seg_meta[i][1], segments[i][:40])
                for i, v in enumerate(translated) if v is None]

    # ---- 第三遍：組裝輸出 ----
    out_lines = []
    for item in plan:
        if item[0] == 'line':
            out_lines.append(item[1])
        elif item[0] == 'block':
            _, seg_idx, indent, originals = item
            text = translated[seg_idx]
            if text is None:
                # 失敗：逐行還原成輸入時的原樣
                out_lines.extend(f"{ind}{comment_char} {content}\n"
                                 for ind, content in originals)
            else:
                wrapped = textwrap.wrap(text, width=76) or [text]
                out_lines.extend(f"{indent}{comment_char} {piece}\n" for piece in wrapped)
        elif item[0] == 'inline':
            _, seg_idx, config_part = item
            text = translated[seg_idx]
            if text is None:
                text = segments[seg_idx]
            out_lines.append(f"{config_part}{comment_char} {text}\n")

    with open(output_file, 'w', encoding='utf-8') as f:
        f.writelines(out_lines)

    s = translator.stats
    print(f"\n🎉 處理完成！新檔案已儲存至: {output_file}")
    print(f"📈 來源統計：Gemini {s['gemini']} 段、gtx 備援 {s['gtx']} 段、"
          f"快取命中 {s['cache_hits']} 段、失敗 {s['failed']} 段。")
    if failures:
        print("⚠️ 以下段落翻譯失敗，已保留英文原文，重跑會自動再試：")
        for line_no, preview in failures:
            print(f"   - 第 {line_no} 行附近：{preview}...")


if __name__ == "__main__":
    print("=" * 50)
    print("  php.ini & httpd.conf 原始檔案全自動中文化工具 (V6)")
    print("=" * 50)
    print("1. 翻譯原始的 php.ini")
    print("2. 翻譯原始的 httpd.conf")
    choice = input("請選擇要翻譯的檔案類型 (輸入 1 或 2): ").strip()

    if choice == '1':
        filename = input("請輸入原本 php.ini 的真實檔名 (例如 php.ini): ").strip()
        translate_config_file(filename, "php_zh.ini", comment_char=';')
    elif choice == '2':
        filename = input("請輸入原本 httpd.conf 的真實檔名 (例如 httpd.conf): ").strip()
        translate_config_file(filename, "httpd_zh.conf", comment_char='#')
    else:
        print("選擇錯誤，程序結束。")
