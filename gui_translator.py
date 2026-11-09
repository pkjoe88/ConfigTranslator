# -*- coding: utf-8 -*-
"""
伺服器設定檔翻譯工具（GUI 版）

這支只是 translator.py 的圖形殼，翻譯邏輯「完全照 translator.py」：
  * 引擎：Gemini 主引擎（繁體中文、台灣口語 prompt）＋ Google gtx 備援
  * 金鑰：環境變數 GEMINI_API_KEYS 池＋GEMINI_API_KEY 後備（與 translator.py 相同）
  * 規則：連續註解先合併成段再翻；被註解掉的參數行（;extension=...）、
    段落標頭不送翻；翻譯快取落盤 translation_cache.json；失敗保留原文並回報
  * 新增功能：左側「匯入檔案」、右側「儲存檔案」、文字框右鍵選單、左右窗格同步捲動與絕對均分大小。

使用方式：把這個檔案和 translator.py 放在同一個資料夾，執行即可。
  python gui_translator.py

僅使用 Python 標準函式庫（tkinter + translator.py）。
"""
import os
import sys
import textwrap
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog

# translator.py 必須在同一個資料夾（或已在模組搜尋路徑上）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from translator import Translator, is_translatable_comment, GeminiEngine, read_lines
except ImportError:
    Translator = None  # 啟動時再明確報錯


def _comment_char_of(line):
    """回傳這行的註解符號（';' 或 '#'），不是註解行就回傳 None。
    行首是註解符號 → 整行註解；行中出現 → 行尾註解。"""
    stripped = line.strip()
    if stripped.startswith(';'):
        return ';'
    if stripped.startswith('#'):
        return '#'
    for ch in (';', '#'):
        if ch in line:
            return ch
    return None


def translate_snippet(text):
    """翻譯一段貼上的設定檔文字。回傳 (譯後文字, 統計 dict, 失敗行清單)。
    規則與 translator.py 完全一致，只是輸入是字串而不是檔案。"""
    lines = text.split('\n')
    # plan：('line', 文字) 原樣；('block', seg_idx, indent, char, originals) 註解段；
    #       ('inline', seg_idx, config_part, char) 設定行＋行尾註解
    plan = []
    segments = []
    seg_meta = []  # (kind, line_no)
    block = []     # (line_no, indent, char, content)

    def flush_block():
        if not block:
            return
        merged = " ".join(content for _, _, _, content in block)
        idx = len(segments)
        segments.append(merged)
        seg_meta.append(('block', block[0][0]))
        plan.append(('block', idx, block[0][1], block[0][2],
                     [(ind, ch, content) for _, ind, ch, content in block]))
        block.clear()

    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        ch = _comment_char_of(line)
        if ch and stripped.startswith(ch):
            content = stripped.lstrip(ch).strip()
            if is_translatable_comment(content):
                # 註解符號換了就先結掉上一段（php.ini 用 ';'、httpd 用 '#'，混貼時別混段）
                if block and block[0][2] != ch:
                    flush_block()
                indent = line[:line.find(ch)]
                block.append((line_no, indent, ch, content))
            else:
                flush_block()
                plan.append(('line', line))
        elif ch and stripped:
            flush_block()
            config_part, comment_part = line.split(ch, 1)
            comment_text = comment_part.strip()
            if is_translatable_comment(comment_text):
                idx = len(segments)
                segments.append(comment_text)
                seg_meta.append(('inline', line_no))
                plan.append(('inline', idx, config_part, ch))
            else:
                plan.append(('line', line))
        else:
            flush_block()
            plan.append(('line', line))
    flush_block()

    translator = Translator()
    translated = translator.translate_all(segments)
    translator.save_cache()

    failures = [(seg_meta[i][1], segments[i][:40])
                for i, v in enumerate(translated) if v is None]

    out_lines = []
    for item in plan:
        if item[0] == 'line':
            out_lines.append(item[1])
        elif item[0] == 'block':
            _, seg_idx, indent, ch, originals = item
            text_out = translated[seg_idx]
            if text_out is None:
                out_lines.extend(f"{ind}{ch} {content}\n"
                                 for ind, _, content in originals)
            else:
                wrapped = textwrap.wrap(text_out, width=76) or [text_out]
                out_lines.extend(f"{indent}{ch} {piece}\n" for piece in wrapped)
        elif item[0] == 'inline':
            _, seg_idx, config_part, ch = item
            text_out = translated[seg_idx]
            if text_out is None:
                text_out = segments[seg_idx]
            out_lines.append(f"{config_part}{ch} {text_out}\n")

    return "\n".join(o.rstrip('\n') for o in out_lines), translator.stats, failures


class ConfigTranslatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("伺服器設定檔翻譯工具 (Gemini 主引擎 + gtx 備援)")
        self.root.geometry("1000x700")

        style = ttk.Style()
        try:
            style.theme_use('vista')
        except tk.TclError:
            pass

        top_frame = ttk.Frame(root, padding="10")
        top_frame.pack(fill=tk.X)

        self.btn_trans = ttk.Button(top_frame, text="🚀 開始翻譯",
                                    command=self.start_translation_thread)
        self.btn_trans.pack(side=tk.LEFT, padx=5)

        self.btn_clear = ttk.Button(top_frame, text="🧹 清空內容",
                                    command=self.clear_text)
        self.btn_clear.pack(side=tk.LEFT, padx=5)

        self.progress_bar = ttk.Progressbar(top_frame, orient="horizontal",
                                            length=200, mode="indeterminate")
        self.status_label = ttk.Label(top_frame, text="",
                                      font=("Microsoft JhengHei", 10))

        # 使用 PanedWindow 讓左右窗格可以自由調整大小，且初始化絕對均分 (50:50)
        main_pane = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ---------------- 左側區域（輸入與匯入） ----------------
        left_frame = ttk.LabelFrame(main_pane, text="原文設定檔 / 英文註解：")
        main_pane.add(left_frame, weight=1)

        left_toolbar = ttk.Frame(left_frame, padding="2")
        left_toolbar.pack(fill=tk.X, padx=5)
        self.btn_import = ttk.Button(left_toolbar, text="📂 匯入檔案", command=self.import_file)
        self.btn_import.pack(side=tk.LEFT, padx=2)

        self.txt_input = scrolledtext.ScrolledText(left_frame, wrap=tk.NONE,
                                                   font=("Consolas", 11), undo=True)
        self.txt_input.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # ---------------- 右側區域（輸出與儲存） ----------------
        right_frame = ttk.LabelFrame(main_pane, text="繁體中文對照譯文：")
        main_pane.add(right_frame, weight=1)

        right_toolbar = ttk.Frame(right_frame, padding="2")
        right_toolbar.pack(fill=tk.X, padx=5)
        self.btn_save = ttk.Button(right_toolbar, text="💾 儲存檔案", command=self.save_file)
        self.btn_save.pack(side=tk.LEFT, padx=2)

        self.txt_output = scrolledtext.ScrolledText(right_frame, wrap=tk.NONE,
                                                    font=("Consolas", 11),
                                                    bg="#fcfcfc", undo=True)
        self.txt_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 綁定右鍵選單
        self.create_context_menu(self.txt_input)
        self.create_context_menu(self.txt_output)

        # ---------------- 雙向同步捲動 ----------------
        self._syncing = False
        self.txt_input.vbar.config(command=self.on_scrollbar_scroll)
        self.txt_output.vbar.config(command=self.on_scrollbar_scroll)

        self.txt_input.bind("<MouseWheel>", lambda e: self.on_mouse_wheel(e, self.txt_input, self.txt_output))
        self.txt_output.bind("<MouseWheel>", lambda e: self.on_mouse_wheel(e, self.txt_output, self.txt_input))

        self.txt_input.bind("<Button-4>", lambda e: self.on_linux_wheel(e, 1, self.txt_input, self.txt_output))
        self.txt_input.bind("<Button-5>", lambda e: self.on_linux_wheel(e, -1, self.txt_input, self.txt_output))
        self.txt_output.bind("<Button-4>", lambda e: self.on_linux_wheel(e, 1, self.txt_output, self.txt_input))
        self.txt_output.bind("<Button-5>", lambda e: self.on_linux_wheel(e, -1, self.txt_output, self.txt_input))

    def on_scrollbar_scroll(self, *args):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.txt_input.yview(*args)
            self.txt_output.yview(*args)
        finally:
            self._syncing = False

    def on_mouse_wheel(self, event, source, target):
        if self._syncing:
            return
        self._syncing = True
        try:
            if sys.platform == "darwin":
                source.yview_scroll(int(-1 * event.delta), "units")
                target.yview_scroll(int(-1 * event.delta), "units")
            else:
                source.yview_scroll(int(-1 * (event.delta / 120)), "units")
                target.yview_scroll(int(-1 * (event.delta / 120)), "units")
        finally:
            self._syncing = False
        return "break"

    def on_linux_wheel(self, event, direction, source, target):
        if self._syncing:
            return
        self._syncing = True
        try:
            source.yview_scroll(direction, "units")
            target.yview_scroll(direction, "units")
        finally:
            self._syncing = False
        return "break"

    def create_context_menu(self, text_widget):
        menu = tk.Menu(text_widget, tearoff=0)
        menu.add_command(label="復原 (Undo)", command=lambda: text_widget.event_generate("<<Undo>>"))
        menu.add_separator()
        menu.add_command(label="剪下 (Cut)", command=lambda: text_widget.event_generate("<<Cut>>"))
        menu.add_command(label="複製 (Copy)", command=lambda: text_widget.event_generate("<<Copy>>"))
        menu.add_command(label="貼上 (Paste)", command=lambda: text_widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="全選 (Select All)", command=lambda: text_widget.tag_add("sel", "1.0", "end"))

        def show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        if sys.platform == "darwin":
            text_widget.bind("<Button-2>", show_menu)
        else:
            text_widget.bind("<Button-3>", show_menu)

    def import_file(self):
        filepath = filedialog.askopenfilename(
            title="選擇要翻譯的設定檔",
            filetypes=[("設定檔與文字檔", "*.ini *.conf *.txt"), ("所有檔案", "*.*")]
        )
        if not filepath:
            return
        try:
            lines, encoding, clean = read_lines(filepath)
            content = "".join(lines)
            self.txt_input.delete("1.0", tk.END)
            self.txt_input.insert(tk.END, content)
            if not clean:
                messagebox.showwarning("編碼警告", f"檔案編碼解碼不完全，已用替代字元讀入（偵測編碼：{encoding}）。")
            else:
                self.status_label.config(text=f"📂 已成功匯入：{os.path.basename(filepath)}（編碼：{encoding}）")
                self.status_label.pack(side=tk.LEFT, padx=10)
        except Exception as e:
            messagebox.showerror("匯入失敗", f"無法讀取檔案：{str(e)}")

    def save_file(self):
        content = self.txt_output.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("提示", "右側沒有可以儲存的譯文內容！")
            return
        filepath = filedialog.asksaveasfilename(
            title="儲存翻譯後的設定檔",
            defaultextension=".ini",
            filetypes=[("INI 設定檔", "*.ini"), ("Conf 設定檔", "*.conf"), ("文字檔", "*.txt"), ("所有檔案", "*.*")]
        )
        if not filepath:
            return
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            messagebox.showinfo("儲存成功", f"檔案已成功儲存至：\n{filepath}")
        except Exception as e:
            messagebox.showerror("儲存失敗", f"無法寫入檔案：{str(e)}")

    def clear_text(self):
        self.txt_input.delete("1.0", tk.END)
        self.txt_output.delete("1.0", tk.END)
        self.status_label.config(text="")
        self.progress_bar.stop()
        self.progress_bar.pack_forget()

    def start_translation_thread(self):
        raw_text = self.txt_input.get("1.0", tk.END)
        if not raw_text.strip():
            messagebox.showwarning("提示", "請先匯入檔案或貼上需要翻譯的文字！")
            return
        self.btn_trans.config(state=tk.DISABLED)
        self.btn_import.config(state=tk.DISABLED)
        self.btn_save.config(state=tk.DISABLED)
        self.status_label.config(text="⏳ 翻譯中…（Gemini 主引擎，失敗會自動走 gtx 備援）")
        self.status_label.pack(side=tk.LEFT, padx=10)
        self.progress_bar.pack(side=tk.LEFT, padx=5)
        self.progress_bar.start(10)

        thread = threading.Thread(target=self.run_translation, args=(raw_text,))
        thread.daemon = True
        thread.start()

    def run_translation(self, raw_text):
        try:
            result, stats, failures = translate_snippet(raw_text)
            self.root.after(0, lambda: self.update_output_ui(result, stats, failures))
        except Exception as e:
            self.root.after(0, lambda: self.show_error(str(e)))

    def show_error(self, msg):
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.status_label.pack_forget()
        self.btn_trans.config(state=tk.NORMAL)
        self.btn_import.config(state=tk.NORMAL)
        self.btn_save.config(state=tk.NORMAL)
        messagebox.showerror("翻譯失敗", f"翻譯過程出錯：{msg}")

    def update_output_ui(self, result, stats, failures):
        self.txt_output.delete("1.0", tk.END)
        self.txt_output.insert(tk.END, result)
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        status = (f"✅ 完成！Gemini {stats['gemini']} 段、"
                  f"gtx {stats['gtx']} 段、快取 {stats['cache_hits']} 段、"
                  f"失敗 {stats['failed']} 段")
        self.status_label.config(text=status)
        self.btn_trans.config(state=tk.NORMAL)
        self.btn_import.config(state=tk.NORMAL)
        self.btn_save.config(state=tk.NORMAL)
        msg = status
        if failures:
            msg += "\n\n以下段落翻譯失敗，已保留英文原文：\n" + "\n".join(
                f"第 {line_no} 行附近：{preview}..." for line_no, preview in failures)
        messagebox.showinfo("翻譯完成", msg)


if __name__ == "__main__":
    if Translator is None:
        tk.Tk().withdraw()
        messagebox.showerror(
            "找不到 translator.py",
            "錯誤：在同一個資料夾下找不到 'translator.py'。\n請確定兩支檔案放在一起。"
        )
        sys.exit(1)

    root = tk.Tk()
    app = ConfigTranslatorGUI(root)
    root.mainloop()
