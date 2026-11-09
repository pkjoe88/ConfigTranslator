# ConfigTranslator — 伺服器設定檔註解中文化工具

> 將 `php.ini` 與 Apache `httpd.conf`（含 `httpd-ssl.conf`、`httpd-vhosts.conf`）中
> 的**英文註解**自動翻譯為**繁體中文（台灣口語）**的桌面／命令列工具。
> 主引擎使用 Google Gemini（可控制口吻），備援使用 Google gtx，並內建翻譯快取
> 與多金鑰輪替機制。

---

## 專案簡介

`ConfigTranslator` 是一套專為伺服器設定檔設計的翻譯工具：它只翻譯**註解文字**，
保留所有指令、參數名、檔名、URL、數字與程式碼片段原樣，避免破壞設定檔語法。
支援兩種使用方式：

- **命令列（CLI）**：`translator.py`，適合批次處理整份設定檔。
- **圖形介面（GUI）**：`gui_translator.py`，提供左右對照、同步捲動、匯入／儲存、右鍵選單等功能。

翻譯結果會寫入新的 `_zh` 檔案（例如 `php_zh.ini`、`httpd_zh.conf`），並把每段
譯文存入 `translation_cache.json`，重跑時可直接命中快取、接續未完成的部分。

---

## 主要功能

- **雙引擎翻譯**
  - 主引擎：Google Gemini（REST API），使用台灣口語／白話 prompt，譯文自然。
  - 備援引擎：Google gtx（`translate.googleapis.com`），Gemini 無金鑰、額度用盡或連線失敗時自動接手。
- **批次翻譯**：將多段註解編號後一次送出（預設 12 段／次），在免費額度限制下盡量減少呼叫次數。
- **多金鑰輪替**：支援 `GEMINI_API_KEYS`（逗號／分號分隔）金鑰池，單把額度用盡時自動標死並換下一把；短暫 429/503 會退避重試。
- **智慧過濾**：被註解掉的參數行（如 `;extension=...`）、段落標頭（`[PHP]`）、`<...>` 標籤行不送翻。
- **連續註解合併**：相鄰註解行先合併成一段再翻，翻譯品質更連貫，輸出時以 76 字元寬度自動折行。
- **行尾註解支援**：設定行後的行尾註解（`directive = value  ; comment`）也會翻譯並保留原設定。
- **編碼自動偵測**：先讀 bytes 再嚴格解碼，依序嘗試 `utf-8-sig`、`utf-8`、`big5`、`gbk`、`cp950`、`cp1252`；失敗時回報不乾淨。
- **翻譯快取**：`translation_cache.json` 落盤，重跑可接續；統計 Gemini／gtx／快取命中／失敗段數。
- **失敗回報**：翻譯失敗的段落保留英文原文，並列出失敗行號與預覽文字。
- **GUI 便利功能**：左右窗格絕對均分（50:50）、雙向同步捲動、右鍵選單（復原／剪下／複製／貼上／全選）、匯入檔案、儲存檔案、清空、進度列。
- **可打包成單一 EXE**：附 `ConfigTranslator.spec`，使用 PyInstaller 打包為無主控台視窗的執行檔。

---

## 資料夾結構

```
translate/
├── translator.py               # 翻譯核心（CLI 入口 + 引擎/快取/設定檔處理）
├── gui_translator.py           # Tkinter 圖形介面（translator.py 的 GUI 殼）
├── ConfigTranslator.spec       # PyInstaller 打包設定
├── README.md                   # 本文件
│
├── php.ini                     # 原始 php.ini（追蹤）
├── php_zh.ini                  # 翻譯後的 php.ini（產生）
├── httpd.conf                  # 原始 Apache 設定（追蹤）
├── httpd_zh.conf               # 翻譯後的 httpd.conf（產生）
├── httpd-ssl.conf              # 原始 SSL 設定（產生/下載）
├── httpd-ssl_zh.conf           # 翻譯後的 SSL 設定（產生）
├── httpd-vhosts.conf           # 原始 vhosts 設定（產生/下載）
├── httpd-vhosts_zh.conf        # 翻譯後的 vhosts 設定（產生）
│
├── translation_cache.json      # 翻譯快取（產生，可跨 CLI/GUI 共用）
│
├── build/                      # PyInstaller 建置中間檔（產生，勿提交）
├── dist/                       # 打包產物（產生，勿提交）
│   ├── ConfigTranslator.exe
│   └── translation_cache.json
└── __pycache__/                # Python 位元組碼快取（產生）
```

---

## 安裝

### 需求

- Python **3.8 以上**（開發／測試環境為 Python 3.13）。
- 僅使用 **Python 標準函式庫**（`tkinter`、`urllib`、`json`、`re`、`threading`、`textwrap` 等），**不需 `pip install` 任何套件**。
- 需要網路連線（Gemini REST API / Google gtx）。
- 若要使用 Gemini 主引擎，需設定環境變數（擇一）：
  - `GEMINI_API_KEYS`：多把金鑰，以逗號或分號分隔（不同 Google Cloud 專案）。
  - `GEMINI_API_KEY`：單把金鑰（後備）。
- 可選：`TRANSLATE_GEMINI_MODEL` 覆寫模型名稱（預設 `gemini-3.6-flash`）。

> 金鑰取得方式請參考 Google AI Studio / Google Cloud。請勿把金鑰寫入程式碼或提交至版本庫；
> 僅透過環境變數提供。

### 取得程式碼

```powershell
cd D:\opencode\translate
```

（本專案為 `D:\opencode` Git 儲存庫下的 `translate/` 子目錄。）

---

## 使用方式

### 1. 命令列（CLI）

```powershell
cd D:\opencode\translate
python translator.py
```

執行後依提示選擇：

```
1. 翻譯原始的 php.ini
2. 翻譯原始的 httpd.conf
請選擇要翻譯的檔案類型 (輸入 1 或 2):
```

接著輸入來源檔名（例如 `php.ini` 或 `httpd.conf`），程式會產生 `php_zh.ini` 或 `httpd_zh.conf`，
並印出統計與失敗段落清單。

也可直接呼叫函式（批次／自動化）：

```python
from translator import translate_config_file
translate_config_file("php.ini", "php_zh.ini", comment_char=";")
translate_config_file("httpd.conf", "httpd_zh.conf", comment_char="#")
```

### 2. 圖形介面（GUI）

```powershell
cd D:\opencode\translate
python gui_translator.py
```

操作流程：

1. 按「📂 匯入檔案」選擇 `.ini` / `.conf` / `.txt`，或直接貼上文字。
2. 按「🚀 開始翻譯」，左側原文、右側譯文同步顯示。
3. 按「💾 儲存檔案」將右側譯文存成新檔。
4. 「🧹 清空內容」可重置兩側文字。

### 3. 打包成 EXE（選用）

需先安裝 PyInstaller：

```powershell
pip install pyinstaller
cd D:\opencode\translate
pyinstaller ConfigTranslator.spec
```

產物位於 `dist/ConfigTranslator.exe`（無主控台視窗）。打包時會一併使用同目錄的
`translation_cache.json`（若存在）。

---

## 技術堆疊

| 類別 | 技術 |
|---|---|
| 語言 | Python 3.8+（標準函式庫） |
| GUI | Tkinter（`ttk`、`scrolledtext`、`filedialog`、`messagebox`） |
| HTTP | `urllib.request`（Gemini REST API、Google gtx） |
| 資料 | `json`（翻譯快取）、`re`（註解／指令辨識） |
| 文字處理 | `textwrap`（76 字元折行）、`os`/`sys` |
| 並行 | `threading`（GUI 翻譯不阻塞介面） |
| 打包 | PyInstaller（`ConfigTranslator.spec`，UPX 壓縮） |
| AI 引擎 | Google Gemini（`gemini-3.6-flash` 預設，可覆寫）、Google gtx 備援 |

---

## 注意事項

- **不需第三方依賴**：僅標準函式庫，請勿額外安裝套件（PyInstaller 僅打包時需要）。
- **金鑰安全**：僅透過環境變數 `GEMINI_API_KEYS` / `GEMINI_API_KEY` 提供，切勿寫入程式碼或提交。
- **網路需求**：Gemini 與 gtx 皆需連線；無網路或無金鑰時，Gemini 段落會交由 gtx 處理，全部失敗則保留原文。
- **口吻限制**：gtx 備援為固定書面腔，只有 Gemini 能依 prompt 輸出台灣口語。
- **免費額度**：Gemini 免費額度以「每專案、每模型、次數」計算；429 回覆若含小時級 `retry in` 會把該把金鑰標死並換下一把，不做長時間等待。
- **thinkingBudget 必關**：Gemini 3.x flash 為 thinking 模型，不關閉會吃光 `maxOutputTokens` 導致譯文截斷（程式已處理）。
- **快取共用**：CLI 與 GUI 共用同一個 `translation_cache.json`；刪除或搬移快取會重新翻譯。
- **輸出編碼**：輸出一律 UTF-8；來源檔編碼自動偵測，若偵測不乾淨會顯示警告。
- **不破壞設定**：僅翻譯註解，指令／參數／URL／數字／程式碼片段保留原文。
- **建置產物**：`build/`、`dist/`、`__pycache__/` 為產生物，建議加入 `.gitignore`，勿提交。
- **版本**：本工具為 V6（沿用 V5 已修的端點、編碼、快取、合併註解等修正）。

---

## 授權

本專案未附授權條款；使用前請向專案擁有者確認。本 README 不包含任何金鑰或機密資訊。