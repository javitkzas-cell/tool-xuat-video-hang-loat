"""subtitle_tool.py — Tool độc lập XUẤT PHỤ ĐỀ SRT

Nhập: file kịch bản (.txt) + file giọng đọc (.mp3/.wav)
Xuất: file .srt căn chỉnh theo giọng đọc, số ký tự/dòng giống tool chính (mặc định 45).

Dùng cùng lõi với app chính: stable_whisper.align → split_by_* → SRT.
Chạy bằng Python trong venv:  venv\\Scripts\\python.exe subtitle_tool.py
"""

import os
import re
import sys
import threading
import traceback

import customtkinter as ctk
from tkinter import filedialog, messagebox

# ---------------------------------------------------------------- Đường dẫn gốc
# Khi đóng gói (PyInstaller onedir), file kèm nằm trong _internal (= sys._MEIPASS);
# đồng thời cho phép user đặt file GHI ĐÈ cạnh .exe. Chạy .py → cạnh script.
_BASE_DIRS = []
if getattr(sys, "frozen", False):
    _BASE_DIRS.append(os.path.dirname(sys.executable))          # cạnh .exe (ưu tiên override)
    _mei = getattr(sys, "_MEIPASS", None)
    if _mei:
        _BASE_DIRS.append(_mei)                                 # thư mục _internal (file kèm)
else:
    _BASE_DIRS.append(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = _BASE_DIRS[0]


def _find_in_bases(*relpaths):
    """Tìm file/thư mục theo danh sách đường dẫn tương đối, trong mọi base dir."""
    for base in _BASE_DIRS:
        for rel in relpaths:
            p = os.path.join(base, rel)
            if os.path.exists(p):
                return p
    return None


# FFmpeg: stable_whisper cần ffmpeg để đọc audio → thêm thư mục chứa ffmpeg.exe vào PATH.
_ffmpeg_exe = _find_in_bases(os.path.join("ffmpeg", "ffmpeg.exe"),
                             os.path.join("ffmpeg", "bin", "ffmpeg.exe"),
                             os.path.join("bin", "ffmpeg.exe"),
                             "ffmpeg.exe")
if _ffmpeg_exe:
    os.environ["PATH"] = os.path.dirname(_ffmpeg_exe) + os.pathsep + os.environ.get("PATH", "")

# Model whisper đi kèm (chạy offline): tìm thư mục 'models'.
MODELS_DIR = _find_in_bases("models") or os.path.join(APP_DIR, "models")

# Ngôn ngữ (khớp app chính)
LANG_MAP = {
    "English": "en", "Vietnamese": "vi", "Spanish": "es", "Portuguese": "pt",
    "French": "fr", "German": "de", "Italian": "it", "Japanese": "ja",
    "Korean": "ko", "Chinese": "zh", "Hindi": "hi", "Arabic": "ar",
    "Russian": "ru", "Indonesian": "id", "Thai": "th",
}

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

ACCENT = "#22c55e"
ACCENT_HOVER = "#16a34a"
CARD = "#1b1e24"
SUBTLE = "#8b8f97"


def _fmt_ts(seconds: float) -> str:
    """Giây → HH:MM:SS,mmm (định dạng SRT)."""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class SubtitleTool(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Xuất Phụ Đề SRT")
        self.geometry("480x600")
        self.minsize(440, 560)
        self.resizable(True, True)

        self.txt_file = ""
        self.voice_file = ""
        self.out_file = ""
        self._model = None
        self._busy = False

        self._build_ui()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=22, pady=(20, 6))
        ctk.CTkLabel(header, text="📝 Xuất Phụ Đề SRT",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header,
                     text="Căn chỉnh kịch bản theo giọng đọc → file .srt",
                     font=ctk.CTkFont(size=12), text_color=SUBTLE).pack(anchor="w", pady=(2, 0))

        # Card: inputs
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        card.pack(fill="x", padx=18, pady=10)
        card.grid_columnconfigure(0, weight=1)

        self.lbl_txt = self._file_row(
            card, 0, "📄", "Kịch bản (.txt)", "Chưa chọn file kịch bản",
            self._pick_txt)
        self.lbl_voice = self._file_row(
            card, 1, "🎤", "Giọng đọc (.mp3/.wav)", "Chưa chọn file giọng",
            self._pick_voice)

        # Card: options
        opt = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        opt.pack(fill="x", padx=18, pady=(0, 10))
        opt.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(opt, text="🌐 Ngôn ngữ", anchor="w",
                     font=ctk.CTkFont(size=13)).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        self.lang_combo = ctk.CTkOptionMenu(opt, values=list(LANG_MAP.keys()),
                                            fg_color="#2b2f36", button_color="#3a3f47",
                                            width=170)
        self.lang_combo.set("English")
        self.lang_combo.grid(row=0, column=1, sticky="e", padx=14, pady=(12, 6))

        ctk.CTkLabel(opt, text="🎚 Chất lượng", anchor="w",
                     font=ctk.CTkFont(size=13)).grid(row=1, column=0, sticky="w", padx=14, pady=6)
        self.model_combo = ctk.CTkOptionMenu(
            opt, values=["tiny (nhanh)", "base (chuẩn)", "small (nét hơn)"],
            fg_color="#2b2f36", button_color="#3a3f47", width=170)
        self.model_combo.set("base (chuẩn)")
        self.model_combo.grid(row=1, column=1, sticky="e", padx=14, pady=6)

        # max chars
        row_c = ctk.CTkFrame(opt, fg_color="transparent")
        row_c.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 12))
        row_c.grid_columnconfigure(1, weight=1)
        self.lbl_chars = ctk.CTkLabel(row_c, text="✂️ Ký tự / dòng: 45",
                                      anchor="w", font=ctk.CTkFont(size=13))
        self.lbl_chars.grid(row=0, column=0, columnspan=2, sticky="w")
        self.char_slider = ctk.CTkSlider(row_c, from_=20, to=90, number_of_steps=70,
                                         command=self._on_chars)
        self.char_slider.set(45)
        self.char_slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Output note
        self.lbl_out = ctk.CTkLabel(self, text="💾 Nơi lưu: cạnh file giọng (tự đặt tên)",
                                    font=ctk.CTkFont(size=11), text_color=SUBTLE)
        self.lbl_out.pack(padx=22, pady=(0, 4), anchor="w")

        # Export button
        self.btn_export = ctk.CTkButton(
            self, text="✨  XUẤT PHỤ ĐỀ SRT", height=46,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._start)
        self.btn_export.pack(fill="x", padx=18, pady=(6, 6))

        # Status
        self.progress = ctk.CTkProgressBar(self, mode="indeterminate", height=6)
        self.progress.pack(fill="x", padx=20, pady=(2, 2))
        self.progress.set(0)
        self.lbl_status = ctk.CTkLabel(self, text="Sẵn sàng.", text_color=SUBTLE,
                                       font=ctk.CTkFont(size=12))
        self.lbl_status.pack(padx=20, pady=(2, 14))

    def _file_row(self, parent, row, icon, title, placeholder, cmd):
        fr = ctk.CTkFrame(parent, fg_color="transparent")
        fr.grid(row=row, column=0, sticky="ew", padx=12, pady=(12 if row == 0 else 6,
                                                                6 if row == 0 else 12))
        fr.grid_columnconfigure(0, weight=1)
        info = ctk.CTkFrame(fr, fg_color="transparent")
        info.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(info, text=f"{icon}  {title}", anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        lbl = ctk.CTkLabel(info, text=placeholder, anchor="w",
                           font=ctk.CTkFont(size=11), text_color=SUBTLE)
        lbl.pack(anchor="w")
        ctk.CTkButton(fr, text="Chọn", width=68, height=32, command=cmd,
                      fg_color="#2b2f36", hover_color="#3a3f47").grid(row=0, column=1, padx=(8, 0))
        return lbl

    # ------------------------------------------------------------ actions
    def _on_chars(self, v):
        self.lbl_chars.configure(text=f"✂️ Ký tự / dòng: {int(v)}")

    def _pick_txt(self):
        f = filedialog.askopenfilename(title="Chọn kịch bản",
                                       filetypes=[("Text", "*.txt"), ("Tất cả", "*.*")])
        if f:
            self.txt_file = f
            self.lbl_txt.configure(text=os.path.basename(f), text_color="white")

    def _pick_voice(self):
        f = filedialog.askopenfilename(title="Chọn giọng đọc",
                                       filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac"),
                                                  ("Tất cả", "*.*")])
        if f:
            self.voice_file = f
            self.lbl_voice.configure(text=os.path.basename(f), text_color="white")
            base = os.path.splitext(os.path.basename(f))[0]
            self.lbl_out.configure(
                text=f"💾 Nơi lưu: {base}.srt  (cạnh file giọng)")

    def _set_status(self, text, color=SUBTLE):
        self.lbl_status.configure(text=text, text_color=color)

    def _start(self):
        if self._busy:
            return
        if not self.txt_file or not os.path.isfile(self.txt_file):
            messagebox.showwarning("Thiếu", "Hãy chọn file kịch bản (.txt).")
            return
        if not self.voice_file or not os.path.isfile(self.voice_file):
            messagebox.showwarning("Thiếu", "Hãy chọn file giọng đọc.")
            return
        self._busy = True
        self.btn_export.configure(state="disabled", text="⏳ ĐANG XỬ LÝ...")
        self.progress.start()
        self._set_status("Bắt đầu…", "#f59e0b")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            lang_code = LANG_MAP.get(self.lang_combo.get(), "en")
            model_size = self.model_combo.get().split()[0]  # tiny/base/small
            max_chars = int(self.char_slider.get())

            with open(self.txt_file, "r", encoding="utf-8") as f:
                script = f.read()
            # Bỏ marker pause <#N#> giống app chính
            script = re.sub(r"<#\d+(?:\.\d+)?#>", "", script).strip()
            if not script:
                raise ValueError("File kịch bản rỗng.")

            self.after(0, self._set_status, "🧠 Nạp AI model…", "#f59e0b")
            import stable_whisper
            if self._model is None:
                # Ưu tiên model đi kèm (offline); nếu không có → tải về cache mặc định.
                if os.path.isfile(os.path.join(MODELS_DIR, f"{model_size}.pt")):
                    self._model = stable_whisper.load_model(model_size, download_root=MODELS_DIR)
                else:
                    self.after(0, self._set_status,
                               "🧠 Tải model (lần đầu, cần internet ~30s)…", "#f59e0b")
                    self._model = stable_whisper.load_model(model_size)

            self.after(0, self._set_status, "🎯 Đang căn chỉnh kịch bản với giọng đọc…", "#f59e0b")
            result = self._model.align(self.voice_file, script,
                                       language=lang_code, verbose=False)

            # Chia dòng GIỐNG app chính
            result.split_by_punctuation([('.', ' '), ('?', ' '), ('!', ' '), (',', ' ')])
            result.split_by_gap(0.5)
            result.merge_by_gap(0.3)
            result.split_by_length(max_chars=max_chars)

            segments = [s for s in result.segments if (s.text or "").strip()]
            if not segments:
                raise ValueError("Không tạo được dòng phụ đề nào (kiểm tra kịch bản/giọng).")

            out_path = os.path.splitext(self.voice_file)[0] + ".srt"
            with open(out_path, "w", encoding="utf-8") as f:
                for i, seg in enumerate(segments, 1):
                    f.write(f"{i}\n")
                    f.write(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}\n")
                    f.write((seg.text or "").strip() + "\n\n")

            self.out_file = out_path
            self.after(0, self._done_ok, out_path, len(segments))
        except Exception as e:
            tb = traceback.format_exc()
            self.after(0, self._done_err, str(e), tb)

    def _done_ok(self, path, n):
        self.progress.stop(); self.progress.set(0)
        self._busy = False
        self.btn_export.configure(state="normal", text="✨  XUẤT PHỤ ĐỀ SRT")
        self._set_status(f"✅ Xong: {n} dòng phụ đề", ACCENT)
        if messagebox.askyesno("Hoàn thành",
                               f"Đã xuất {n} dòng phụ đề:\n{path}\n\nMở thư mục chứa file?"):
            try:
                os.startfile(os.path.dirname(path))
            except Exception:
                pass

    def _done_err(self, msg, tb):
        self.progress.stop(); self.progress.set(0)
        self._busy = False
        self.btn_export.configure(state="normal", text="✨  XUẤT PHỤ ĐỀ SRT")
        self._set_status("❌ Lỗi — xem chi tiết", "#ef4444")
        low = msg.lower()
        hint = ""
        if "ffmpeg" in low or "winerror 2" in low or "no such file" in low:
            hint = "\n\n💡 Có thể thiếu FFmpeg. Đặt thư mục 'ffmpeg' cạnh file này."
        elif "stable_whisper" in low or "torch" in low or "modulenotfound" in low:
            hint = "\n\n💡 Chạy bằng Python trong venv:\n  venv\\Scripts\\python.exe subtitle_tool.py"
        messagebox.showerror("Lỗi", f"{msg}{hint}\n\n---\n{tb[-800:]}")


if __name__ == "__main__":
    SubtitleTool().mainloop()
