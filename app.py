import os
import random
import subprocess
import threading
import math
import re
import time
import sys
import tempfile
import logging
import json
import hashlib
import bisect
import traceback
import queue as queue_module

# --- APP INFO ---
APP_VERSION = "2.0.1"
APP_NAME = "Vạn Phẩm - Batch Render Engine"
GITHUB_REPO = "javitkzas-cell/tool-xuat-video-hang-loat"  # ← Thay bằng repo GitHub của bạn (vd "minhchinh/van-pham")
UPDATE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# Các file CODE được thay khi tự cập nhật — KHÔNG đụng venv/user_settings.json/
# bgm/overlay/models/ffmpeg/output... (dữ liệu riêng của từng máy giữ nguyên)
UPDATE_CODE_FILES = [
    'app.py', 'layout_composer.py', 'debug_logger.py', 'subtitle_tool.py',
    'requirements.txt', 'run.bat', 'run_subtitle.bat',
    '2_CAI_DAT_MAY_MOI.bat', 'HUONG_DAN_CAI_DAT_MAY_MOI.txt',
    'icon.ico', 'TAO_SHORTCUT.bat', 'HUONG_DAN_BAO_LOI_DISCORD.txt',
    'CAI_TACH_NEN.bat', 'CAI_TORCH_GPU.bat', 'KIEM_TRA_GPU.bat',
]
# --- BÁO LỖI TỪ XA (Discord webhook) ---
# Dán link webhook Discord của bạn vào đây → mọi máy khác gặp lỗi sẽ tự gửi
# báo cáo (phiên bản, tên máy, traceback) về kênh Discord để bạn đọc & sửa ngay.
# Cách lấy: Discord → Server Settings → Integrations → Webhooks → New Webhook →
# Copy Webhook URL. (Xem HUONG_DAN_BAO_LOI_DISCORD.txt)
DISCORD_WEBHOOK_URL = ""
# Nếu thư mục tool có file tên "DAY_LA_MAY_DEV.txt" → KHÔNG gửi lỗi (máy của bạn
# tự sửa được, khỏi tự spam chính mình). Đặt file này trên (các) máy của bạn.
DEV_MACHINE_MARKER = "DAY_LA_MAY_DEV.txt"

# --- LIỀU THUỐC TRỊ LỖI NONETYPE KHI ẨN MÀN HÌNH ĐEN ---
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# --- ĐƯỜNG DẪN GỐC ---
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# --- THƯ MỤC TẠM ---
TEMP_DIR = os.path.join(tempfile.gettempdir(), "chosen_one_temp")
os.makedirs(TEMP_DIR, exist_ok=True)

# --- LOG FILE ---
LOG_FILE = os.path.join(APP_DIR, "app_debug.log")
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', encoding='utf-8'
)
logging.info(f"=== {APP_NAME} v{APP_VERSION} started ===")

# --- COMPREHENSIVE DEBUG LOGGER ---
# Hệ thống debug riêng: ghi mọi lỗi vào folder debug_logs/ với traceback
# đầy đủ, system info, FFmpeg commands + stderr. User có thể xuất zip bundle.
try:
    import debug_logger
    debug_logger.setup_debug_logger(APP_DIR)
    DEBUG_LOGGER_AVAILABLE = True
except Exception as _dbg_err:
    debug_logger = None
    DEBUG_LOGGER_AVAILABLE = False
    logging.warning(f"debug_logger không khởi tạo được: {_dbg_err}")

# --- TÌM FFMPEG ---
def _find_ffmpeg():
    """Tìm ffmpeg: cạnh app.py → thư mục CHA (chống copy bị lồng thư mục) → PATH."""
    bases = [APP_DIR, os.path.dirname(APP_DIR)]
    for base in bases:
        for sub in ['ffmpeg', 'ffmpeg/bin', 'bin']:
            d = os.path.join(base, sub)
            ff = os.path.join(d, 'ffmpeg.exe')
            if os.path.isfile(ff):
                logging.info(f"FFmpeg found: {d}")
                return d
    # Kiểm tra PATH
    try:
        r = subprocess.run(['ffmpeg', '-version'], capture_output=True, creationflags=0x08000000)
        if r.returncode == 0:
            logging.info("FFmpeg found in PATH")
            return None  # None = dùng PATH
    except FileNotFoundError:
        pass
    return False  # False = không tìm thấy

FFMPEG_DIR = _find_ffmpeg()

# QUAN TRỌNG: bơm thư mục ffmpeg đi kèm vào PATH của process.
# Các thư viện con (whisper / stable-ts / rembg...) tự gọi 'ffmpeg'/'ffprobe'
# bằng tên trần từ PATH — máy mới chưa cài ffmpeg hệ thống sẽ chết WinError 2
# nếu thiếu dòng này.
if FFMPEG_DIR:
    os.environ['PATH'] = FFMPEG_DIR + os.pathsep + os.environ.get('PATH', '')
    logging.info(f"FFmpeg dir added to PATH: {FFMPEG_DIR}")

def get_ffmpeg(name='ffmpeg'):
    """Trả về đường dẫn đầy đủ tới ffmpeg/ffprobe/ffplay."""
    if FFMPEG_DIR is None:
        return name  # Dùng PATH
    if FFMPEG_DIR is False:
        return name  # Không tìm thấy, thử PATH anyway
    return os.path.join(FFMPEG_DIR, f'{name}.exe')

# --- IMPORT THƯ VIỆN ---
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

# --- TRÌNH KÉO-THẢ BỐ CỤC (dùng chung cho CẢ 2 mode) ---
REQUIRED_COMPOSER_VERSION = "2.3"   # phải khớp COMPOSER_VERSION trong layout_composer.py
try:
    from layout_composer import LayoutComposer
    try:
        from layout_composer import COMPOSER_VERSION as _COMPOSER_VER
    except ImportError:
        _COMPOSER_VER = "cũ (trước 2.0)"
    COMPOSER_AVAILABLE = True
except Exception as _comp_err:
    LayoutComposer = None
    _COMPOSER_VER = None
    COMPOSER_AVAILABLE = False
    logging.warning(f"layout_composer không nạp được: {_comp_err}")

try:
    import cv2
    import numpy as np
    from PIL import Image
except ImportError:
    messagebox.showerror("Thiếu thư viện", "Vui lòng chạy lệnh:\npip install opencv-python numpy Pillow")
    exit()

# --- GLOBAL ERROR HANDLER ---
def global_exception_handler(exc_type, exc_value, exc_tb):
    import traceback
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.error(f"UNHANDLED EXCEPTION:\n{error_msg}")
    # Ghi crash log
    crash_file = os.path.join(APP_DIR, "crash_log.txt")
    with open(crash_file, 'w', encoding='utf-8') as f:
        f.write(f"{APP_NAME} v{APP_VERSION}\n")
        f.write(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(error_msg)
    try:
        messagebox.showerror("Lỗi nghiêm trọng", 
            f"Ứng dụng gặp lỗi không mong muốn.\n\n"
            f"Chi tiết đã lưu tại:\n{crash_file}\n\n"
            f"Gửi file này cho nhà phát triển để được hỗ trợ.")
    except: pass

sys.excepthook = global_exception_handler

# --- CẤU HÌNH GIAO DIỆN ---
ctk.set_appearance_mode("Dark")  
ctk.set_default_color_theme("blue")

class VideoGeneratorApp(ctk.CTk):
    def __init__(self):
        # Khai báo AppUserModelID RIÊNG → Windows tách khỏi pythonw.exe và
        # dùng icon cửa sổ cho taskbar (thiếu bước này taskbar hiện icon Python).
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VanPham.BatchRender")
        except Exception:
            pass
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        # Bắt MỌI lỗi callback của Tkinter → log + gửi báo cáo về Discord
        try:
            self.report_callback_exception = self._on_tk_exception
        except Exception:
            pass
        # Icon cửa sổ + taskbar (icon.ico cạnh app.py)
        try:
            _ico = os.path.join(APP_DIR, 'icon.ico')
            if os.path.exists(_ico):
                self.iconbitmap(_ico)
                # iconphoto giúp 1 số bản Windows lấy đúng icon cho taskbar
                try:
                    from PIL import Image, ImageTk
                    self._taskbar_icon_img = ImageTk.PhotoImage(Image.open(_ico))
                    self.iconphoto(True, self._taskbar_icon_img)
                except Exception:
                    pass
                self.after(700, lambda: self.iconbitmap(_ico))   # CTk hay reset → đặt lại
                self.after(1500, lambda: self.iconbitmap(_ico))
        except Exception:
            pass
        self.geometry("1700x900")   # kích thước khi user bấm "khôi phục" (restore)
        self.resizable(True, True)
        self.minsize(1100, 600)
        # Mặc định mở FULL MÀN HÌNH (maximize) — vẫn kéo giãn/khôi phục được.
        # LƯU Ý: phải đặt SAU khi CustomTkinter áp geometry/scaling ban đầu
        # (CTk re-apply geometry lúc khởi động → gọi ngay sẽ bị reset về 1700x900),
        # nên hẹn giờ + tự kiểm tra lại vài lần cho chắc.
        self._maximize_attempts = 0
        self.after(200, self._maximize_startup)

        # Dọn rác file tạm còn sót từ phiên trước (app crash / tắt cứng / mất điện
        # → cleanup lúc thoát không chạy được, file nặng tồn đọng chiếm ổ C)
        self.cleanup_temp_files()

        # Biến cấu hình file/folder
        self.img_folder = ""
        self.voice_file = ""
        self.txt_file = ""
        self.output_folder = "" 

        # Overlay CỤ THỂ user chọn (rỗng = random từ thư mục overlay như cũ)
        self.overlay_specific_file = ""
        # Gắn cứng thư mục BGM và Overlay
        self.bgm_folder = os.path.join(APP_DIR, "bgm")
        self.overlay_folder = os.path.join(APP_DIR, "overlay")
        
        # Gắn cứng SFX
        self.sfx_wipe_file = os.path.join(APP_DIR, "wipe.mp3") 
        self.sfx_blink_file = os.path.join(APP_DIR, "blink.mp3")
        
        # Gắn cứng thư mục CID Music (nhạc cuối video)
        self.cid_folder = os.path.join(APP_DIR, "cid")

        # --- RENDER MODE (chế độ tạo video) ---
        # "ken_burns"  : kịch bản + ảnh (Ken Burns) — chế độ gốc
        # "jesus_split": chân dung Jesus (PNG trong suốt) bên trái + video nền random + phụ đề bên phải
        self.render_mode = "ken_burns"
        # Thư mục video nền stock (Pixabay) — random mỗi video
        self.bg_video_folder = os.path.join(APP_DIR, "bg_videos")
        # --- CHỈNH NỀN VIDEO (mode Tạo từ Video nền) ---
        self.bg_zoom = 112    # % phóng to nền (100 = vừa khít, 112 = punch-in mặc định)
        self.bg_off_x = 0     # dịch ngang -100 (sát trái) .. 100 (sát phải), 0 = giữa
        self.bg_off_y = 0     # dịch dọc  -100 (sát trên) .. 100 (sát dưới), 0 = giữa
        # Ánh sáng + effect nền video (mặc định = màu grade cũ của tool)
        self.bg_brightness = 0     # -50..50
        self.bg_contrast = 103     # 50..150 (%)
        self.bg_saturation = 108   # 0..200 (%)
        self.bg_effect = 'none'    # none|warm|cool|bw|sepia|vignette
        # Thư mục ảnh chân dung Jesus (PNG nền trong suốt) — random mỗi video
        self.jesus_folder = os.path.join(APP_DIR, "jesus")
        # Ảnh Jesus CỤ THỂ cho video kế tiếp (rỗng = random từ thư mục)
        self.jesus_specific_file = ""
        # --- TÁCH NỀN ảnh Jesus (user kiểm soát) ---
        self.jesus_key_method = "auto"   # auto | rembg | grabcut | color | none
        self.jesus_key_erode = 0         # co rìa N px (xoá lem nền)
        self.jesus_key_feather = 1.0     # làm mượt rìa (gauss sigma)
        self.jesus_key_fine = False      # True = tách MỊN (alpha matting, rìa tóc, chậm hơn)
        self._rembg_session = None       # cache model rembg
        # Thời lượng crossfade (xfade) giữa các clip nền, giây. Đặt 0 = cắt cứng.
        self.jesus_xfade_dur = 0.5
        # Độ mạnh particle overlay phủ lên NỀN (0=tắt, 1=mạnh). Blend trong RGB nên KHÔNG ám màu.
        self.jesus_overlay_opacity = 0.5
        # --- BỐ CỤC mode Jesus (phân số theo W/H, chỉnh bằng editor kéo-thả) ---
        # Jesus: x,y = góc trên-trái; h = chiều cao (width tự theo tỉ lệ ảnh)
        self.jesus_layout = {'x': 0.02, 'y': 0.05, 'h': 0.95}
        # Khung chữ: x,y = góc trên-trái; w = chiều rộng (auto-wrap, căn giữa ngang, neo trên)
        self.text_layout = {'x': 0.50, 'y': 0.34, 'w': 0.46}

        # --- LAYOUT COMPOSER (chế độ Ken Burns): layer overlay do user thêm ---
        self.TEMP_DIR = TEMP_DIR              # cho LayoutComposer truy cập
        self.overlay_layers = []              # list dict: logo / subscribe / phông xanh
        self._layer_counter = 0
        # Ô phụ đề Ken Burns (kéo-thả/resize). enabled=False → giữ canh giữa mặc định.
        # Vị trí mặc định mô phỏng kiểu canh giữa (giữa khung) để khớp xem trước.
        self.kb_text_layout = {'x': 0.08, 'y': 0.40, 'w': 0.84, 'h': 0.20, 'enabled': False}
        self.inline_composer = None           # instance LayoutComposer nhúng panel phải
        self.last_dir_bgvid = "/"
        self.last_dir_jesus = "/"

        self.output_folder = "" 

        # BIẾN GHI NHỚ ĐƯỜNG DẪN 
        self.last_dir_main = "/"      
        self.last_dir_bgm = "/"       
        self.last_dir_overlay = "/"   
        self.last_dir_sfx = "/"       

        # BIẾN HÀNG ĐỢI & AN TOÀN HỆ THỐNG
        self.render_queue = []
        self.is_rendering = False
        self.current_task = None
        self.cancel_render = False        
        self.active_processes = []
        
        # PIPELINE: Pre-process video tiếp theo trong nền
        self._preprocess_result = {}   # {voice_file: dict dữ liệu đã xử lý}
        self._preprocess_thread = None

        # RENDER STAGE STATE — cho animate_rendering_screen hiển thị chi tiết
        self._render_stage = "idle"       # "whisper" | "video" | "cid" | "idle"
        self._render_stage_detail = ""    # mô tả chi tiết bước hiện tại
        self._render_overall_pct = 0.0    # progress tổng [0..1]
        self._render_start_time = None    # time.time() khi bắt đầu task hiện tại

        self.preview_timer = None
        self.empty_img = ctk.CTkImage(Image.new("RGB", (1, 1), "black"), size=(1, 1))

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.setup_ui()

        # --- KHÔI PHỤC CÀI ĐẶT PHIÊN TRƯỚC + TỰ ĐỘNG LƯU ---
        self.after(300, self._load_user_settings)
        self.after(4000, self._settings_autosave_tick)

        # --- STARTUP CHECKS ---
        self.after(500, self._startup_checks)

    def setup_ui(self):
        import tkinter as tk
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Title bar with ⚡ button
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, pady=10, sticky="ew")
        title_frame.grid_columnconfigure(0, weight=1)
        
        title_label = ctk.CTkLabel(title_frame, text="🎬 VẠN PHẨM - BATCH RENDER ENGINE", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.grid(row=0, column=0)
        
        # Version + Update
        ver_frame = ctk.CTkFrame(title_frame, fg_color="transparent")
        ver_frame.grid(row=0, column=1, padx=(10, 0))
        
        self.lbl_version = ctk.CTkLabel(ver_frame, text=f"v{APP_VERSION}", text_color="gray", font=ctk.CTkFont(size=11))
        self.lbl_version.pack()
        self.btn_update = ctk.CTkButton(ver_frame, text="🔄", width=28, height=20, corner_radius=10,
                                         fg_color="transparent", hover_color="#333333", text_color="gray",
                                         font=ctk.CTkFont(size=11), command=self.check_for_updates)
        self.btn_update.pack()
        
        self.btn_speed = ctk.CTkButton(
            title_frame, text="⚡", width=36, height=36, corner_radius=18,
            fg_color="#e67e22", hover_color="#f39c12",
            font=ctk.CTkFont(size=18), command=self.open_speed_panel
        )
        self.btn_speed.grid(row=0, column=2, padx=(5, 15))

        # ==========================================
        # 2 MÀN HÌNH CHÍNH (tab):
        #   🎬 DỰNG VIDEO      — thêm/chỉnh thành phần, preview, cài đặt
        #   📊 XUẤT & HÀNG CHỜ — tiến độ video đang xuất + danh sách hàng chờ
        # ==========================================
        self.SCREEN_BUILD = "🎬 DỰNG VIDEO"
        self.SCREEN_OUTPUT = "📊 XUẤT VIDEO & HÀNG CHỜ"
        self.screen_tabs = ctk.CTkTabview(self)
        self.screen_tabs.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.tab_build = self.screen_tabs.add(self.SCREEN_BUILD)
        self.tab_output = self.screen_tabs.add(self.SCREEN_OUTPUT)
        self.screen_tabs.set(self.SCREEN_BUILD)

        # ==========================================
        # PANED WINDOW màn DỰNG VIDEO: Ngang (Trái | Phải)
        # ==========================================
        self.main_paned = tk.PanedWindow(
            self.tab_build, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.RAISED,
            bg="#2b2b2b", borderwidth=0
        )
        self.main_paned.pack(fill="both", expand=True)

        # ==========================================
        # PHÂN VÙNG 1: PANEL NHẬP LIỆU (Trái)
        # ==========================================
        frame_left = ctk.CTkFrame(self.main_paned)
        # width=700 đảm bảo voice source row đủ chỗ cho segmented + nút Quản lý giọng
        # Panel trái GỌN (như editor chuyên nghiệp) — preview bên phải chiếm phần lớn
        self.main_paned.add(frame_left, minsize=340, width=780, stretch="never")

        # --- Scrollable frame cho nội dung nhập liệu ---
        scroll_container = ctk.CTkScrollableFrame(frame_left, fg_color="transparent")
        scroll_container.pack(fill="both", expand=True, padx=5, pady=5)

        lbl_section_input = ctk.CTkLabel(scroll_container, text="📁 CHỌN DỮ LIỆU", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        lbl_section_input.pack(pady=(5, 5), anchor="w", padx=5)

        # --- CHẾ ĐỘ TẠO VIDEO ---
        mode_frame = ctk.CTkFrame(scroll_container, fg_color="transparent")
        mode_frame.pack(pady=(0, 6), padx=5, fill="x")
        ctk.CTkLabel(mode_frame, text="🎞️ Chế độ:", width=80, anchor="w",
                     font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 5))
        self.render_mode_seg = ctk.CTkSegmentedButton(
            mode_frame,
            values=["🖼️ Tạo từ Ảnh", "🎬 Tạo từ Video nền"],
            command=self._on_render_mode_change
        )
        self.render_mode_seg.set("🖼️ Tạo từ Ảnh")
        self.render_mode_seg.pack(side="left", fill="x", expand=True)

        frame_inputs = ctk.CTkFrame(scroll_container, fg_color="transparent")
        frame_inputs.pack(pady=5, padx=5, fill="x")
        frame_inputs.grid_columnconfigure(1, weight=1)

        self.btn_img = ctk.CTkButton(frame_inputs, text="📂 Chọn thư mục Ảnh", command=self.select_img_folder, width=180)
        self.btn_img.grid(row=0, column=0, padx=5, pady=6)
        self.lbl_img = ctk.CTkLabel(frame_inputs, text="Chưa chọn...", text_color="gray", anchor="w")
        self.lbl_img.grid(row=0, column=1, sticky="ew", padx=5)

        # --- VOICE: chọn file giọng đọc có sẵn ---
        self.voice_file_frame = ctk.CTkFrame(frame_inputs, fg_color="transparent")
        self.voice_file_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=(6, 6))
        self.voice_file_frame.grid_columnconfigure(1, weight=1)
        self.btn_voice = ctk.CTkButton(self.voice_file_frame, text="🎤 Chọn Voice", command=self.select_voice, width=180)
        self.btn_voice.grid(row=0, column=0, padx=5, pady=2)
        self.lbl_voice = ctk.CTkLabel(self.voice_file_frame, text="Chưa chọn...", text_color="gray", anchor="w")
        self.lbl_voice.grid(row=0, column=1, sticky="ew", padx=5)

        self.btn_txt = ctk.CTkButton(frame_inputs, text="📝 Chọn Kịch Bản (TXT)", command=self.select_txt, width=180, fg_color="#8e44ad")
        self.btn_txt.grid(row=3, column=0, padx=5, pady=6)
        self.lbl_txt = ctk.CTkLabel(frame_inputs, text="Chưa chọn...", text_color="gray", anchor="w")
        self.lbl_txt.grid(row=3, column=1, sticky="ew", padx=5)

        self.btn_out = ctk.CTkButton(frame_inputs, text="💾 Nơi lưu Video", command=self.select_out_folder, width=180, fg_color="#2c3e50")
        self.btn_out.grid(row=4, column=0, padx=5, pady=6)
        self.lbl_out = ctk.CTkLabel(frame_inputs, text="Mặc định", text_color="gray", anchor="w")
        self.lbl_out.grid(row=4, column=1, sticky="ew", padx=5)

        lbl_name_hint = ctk.CTkLabel(frame_inputs, text="Tên Video xuất:", width=180, anchor="e", font=ctk.CTkFont(weight="bold"), text_color="#3498db")
        lbl_name_hint.grid(row=5, column=0, padx=5, pady=6)
        self.entry_vid_name = ctk.CTkEntry(frame_inputs, placeholder_text="Để trống lấy tên Voice...")
        self.entry_vid_name.grid(row=5, column=1, padx=5, pady=6, sticky="ew")

        # Nút mở trình kéo-thả bố cục — dùng cho CẢ 2 mode, luôn hiển thị
        self.btn_layout_editor = ctk.CTkButton(
            frame_inputs, text="🎚️ Mở trình kéo-thả bố cục",
            command=self._open_composer_from_button, fg_color="#d35400", hover_color="#e67e22",
            font=ctk.CTkFont(weight="bold"))
        self.btn_layout_editor.grid(row=6, column=0, columnspan=2, sticky="ew", padx=5, pady=(6, 6))

        # --- OVERLAY CỤ THỂ (particle) — rỗng = random từ thư mục overlay ---
        self.btn_overlay_pick = ctk.CTkButton(frame_inputs, text="✨ Chọn Overlay cụ thể",
                                              command=self.select_overlay_specific,
                                              width=180, fg_color="#16a085")
        self.btn_overlay_pick.grid(row=7, column=0, padx=5, pady=6)
        _ov_row = ctk.CTkFrame(frame_inputs, fg_color="transparent")
        _ov_row.grid(row=7, column=1, sticky="ew", padx=5)
        _ov_row.grid_columnconfigure(0, weight=1)
        self.lbl_overlay_pick = ctk.CTkLabel(_ov_row, text="🎲 Random từ thư mục overlay",
                                             text_color="gray", anchor="w")
        self.lbl_overlay_pick.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(_ov_row, text="🎲", width=34, fg_color="gray30", hover_color="gray40",
                      command=self.clear_overlay_specific).grid(row=0, column=1, padx=(4, 0))

        # --- CHỈNH NỀN VIDEO (chỉ hiện ở mode Tạo từ Video nền) ---
        self.bg_adjust_frame = ctk.CTkFrame(scroll_container, fg_color=("#2b2b2b", "#1f2937"),
                                            border_width=1, border_color="#16a085")
        # Không pack ngay — chỉ hiện khi chọn mode video nền
        self.bg_adjust_frame.grid_columnconfigure(1, weight=1)
        _bga_title = ctk.CTkFrame(self.bg_adjust_frame, fg_color="transparent")
        _bga_title.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(6, 2))
        _bga_title.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(_bga_title, text="🎛️ CHỈNH NỀN VIDEO",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color="#48c9b0",
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.sw_xfade = ctk.CTkSwitch(_bga_title, text="Xfade", width=68,
                                      font=ctk.CTkFont(size=11),
                                      command=self._on_xfade_toggle)
        self.sw_xfade.select()     # mặc định BẬT (chuyển cảnh mượt)
        self.sw_xfade.grid(row=0, column=2, sticky="e", padx=(0, 6))
        ctk.CTkButton(_bga_title, text="↺ Mặc định", width=90, height=22,
                      fg_color="gray30", hover_color="gray40",
                      command=self._reset_bg_adjust).grid(row=0, column=3, sticky="e")

        ctk.CTkLabel(self.bg_adjust_frame, text="🔍 Phóng to:", width=100, anchor="w"
                     ).grid(row=1, column=0, padx=(8, 2), pady=2, sticky="w")
        self.slider_bg_zoom = ctk.CTkSlider(self.bg_adjust_frame, from_=100, to=200,
                                            number_of_steps=100, command=self._on_bg_zoom)
        self.slider_bg_zoom.set(self.bg_zoom)
        self.slider_bg_zoom.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        self.lbl_bg_zoom = ctk.CTkLabel(self.bg_adjust_frame, text=f"{self.bg_zoom}%",
                                        width=52, anchor="e")
        self.lbl_bg_zoom.grid(row=1, column=2, padx=(2, 10), pady=2)

        ctk.CTkLabel(self.bg_adjust_frame, text="↔ Dịch ngang X:", width=100, anchor="w"
                     ).grid(row=2, column=0, padx=(8, 2), pady=2, sticky="w")
        self.slider_bg_off_x = ctk.CTkSlider(self.bg_adjust_frame, from_=-100, to=100,
                                             number_of_steps=200, command=self._on_bg_off_x)
        self.slider_bg_off_x.set(self.bg_off_x)
        self.slider_bg_off_x.grid(row=2, column=1, sticky="ew", padx=2, pady=2)
        self.lbl_bg_off_x = ctk.CTkLabel(self.bg_adjust_frame, text=f"{self.bg_off_x}",
                                         width=52, anchor="e")
        self.lbl_bg_off_x.grid(row=2, column=2, padx=(2, 10), pady=2)

        ctk.CTkLabel(self.bg_adjust_frame, text="↕ Dịch dọc Y:", width=100, anchor="w"
                     ).grid(row=3, column=0, padx=(8, 2), pady=2, sticky="w")
        self.slider_bg_off_y = ctk.CTkSlider(self.bg_adjust_frame, from_=-100, to=100,
                                             number_of_steps=200, command=self._on_bg_off_y)
        self.slider_bg_off_y.set(self.bg_off_y)
        self.slider_bg_off_y.grid(row=3, column=1, sticky="ew", padx=2, pady=2)
        self.lbl_bg_off_y = ctk.CTkLabel(self.bg_adjust_frame, text=f"{self.bg_off_y}",
                                         width=52, anchor="e")
        self.lbl_bg_off_y.grid(row=3, column=2, padx=(2, 10), pady=2)

        # --- Ánh sáng ---
        ctk.CTkLabel(self.bg_adjust_frame, text="☀ Độ sáng:", width=100, anchor="w"
                     ).grid(row=4, column=0, padx=(8, 2), pady=2, sticky="w")
        self.slider_bg_bright = ctk.CTkSlider(self.bg_adjust_frame, from_=-50, to=50,
                                              number_of_steps=100, command=self._on_bg_bright)
        self.slider_bg_bright.set(self.bg_brightness)
        self.slider_bg_bright.grid(row=4, column=1, sticky="ew", padx=2, pady=2)
        self.lbl_bg_bright = ctk.CTkLabel(self.bg_adjust_frame, text="0", width=52, anchor="e")
        self.lbl_bg_bright.grid(row=4, column=2, padx=(2, 10), pady=2)

        ctk.CTkLabel(self.bg_adjust_frame, text="◐ Tương phản:", width=100, anchor="w"
                     ).grid(row=5, column=0, padx=(8, 2), pady=2, sticky="w")
        self.slider_bg_contrast = ctk.CTkSlider(self.bg_adjust_frame, from_=50, to=150,
                                                number_of_steps=100, command=self._on_bg_contrast)
        self.slider_bg_contrast.set(self.bg_contrast)
        self.slider_bg_contrast.grid(row=5, column=1, sticky="ew", padx=2, pady=2)
        self.lbl_bg_contrast = ctk.CTkLabel(self.bg_adjust_frame, text="103%", width=52, anchor="e")
        self.lbl_bg_contrast.grid(row=5, column=2, padx=(2, 10), pady=2)

        ctk.CTkLabel(self.bg_adjust_frame, text="🎨 Bão hòa màu:", width=100, anchor="w"
                     ).grid(row=6, column=0, padx=(8, 2), pady=2, sticky="w")
        self.slider_bg_sat = ctk.CTkSlider(self.bg_adjust_frame, from_=0, to=200,
                                           number_of_steps=200, command=self._on_bg_sat)
        self.slider_bg_sat.set(self.bg_saturation)
        self.slider_bg_sat.grid(row=6, column=1, sticky="ew", padx=2, pady=2)
        self.lbl_bg_sat = ctk.CTkLabel(self.bg_adjust_frame, text="108%", width=52, anchor="e")
        self.lbl_bg_sat.grid(row=6, column=2, padx=(2, 10), pady=2)

        # --- Effect ---
        ctk.CTkLabel(self.bg_adjust_frame, text="✨ Effect:", width=100, anchor="w"
                     ).grid(row=7, column=0, padx=(8, 2), pady=2, sticky="w")
        self.BG_EFFECT_MAP = {"Không": "none", "Ấm áp": "warm", "Lạnh": "cool",
                              "Đen trắng": "bw", "Sepia": "sepia", "Vignette": "vignette"}
        self.bg_effect_combo = ctk.CTkOptionMenu(
            self.bg_adjust_frame, values=list(self.BG_EFFECT_MAP.keys()),
            command=self._on_bg_effect, height=26)
        self.bg_effect_combo.set("Không")
        self.bg_effect_combo.grid(row=7, column=1, columnspan=2, sticky="ew",
                                  padx=(2, 10), pady=2)

        ctk.CTkLabel(self.bg_adjust_frame,
                     text="• 100% = vừa khít khung. Phóng to rồi kéo X/Y để chọn vùng hiển thị.\n"
                          "• Ánh sáng/effect hiện TRỰC TIẾP trên màn hình Preview (bấm ▶ Phát).\n"
                          "• Giá trị được áp dụng khi THÊM VÀO HÀNG ĐỢI.",
                     text_color="#7f8c8d", font=ctk.CTkFont(size=10), justify="left", anchor="w"
                     ).grid(row=8, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))

        # --- Tùy chỉnh subtitle ---
        lbl_section_sub = ctk.CTkLabel(scroll_container, text="✏️ TÙY CHỈNH PHỤ ĐỀ", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        lbl_section_sub.pack(pady=(15, 5), anchor="w", padx=5)
        self._sub_section_lbl = lbl_section_sub  # mốc để pack khung Jesus đúng vị trí

        frame_opts = ctk.CTkFrame(scroll_container)
        frame_opts.pack(pady=5, padx=5, fill="x")
        frame_opts.grid_columnconfigure(1, weight=1)

        lbl_outline = ctk.CTkLabel(frame_opts, text="Độ đậm viền:", font=ctk.CTkFont(weight="bold"))
        lbl_outline.grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.slider_outline = ctk.CTkSlider(frame_opts, from_=0, to=5, number_of_steps=50, command=self.update_outline_lbl)
        self.slider_outline.set(4.0)
        self.slider_outline.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.lbl_outline_val = ctk.CTkLabel(frame_opts, text="4.0", width=30)
        self.lbl_outline_val.grid(row=0, column=2, padx=5, pady=5)

        lbl_fontsize = ctk.CTkLabel(frame_opts, text="Cỡ chữ:", font=ctk.CTkFont(weight="bold"))
        lbl_fontsize.grid(row=1, column=0, padx=10, pady=5, sticky="e")
        self.slider_fontsize = ctk.CTkSlider(frame_opts, from_=10, to=300, number_of_steps=290, command=self.update_fontsize_lbl)
        self.slider_fontsize.set(55)
        self.slider_fontsize.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.lbl_fontsize_val = ctk.CTkLabel(frame_opts, text="55", width=30)
        self.lbl_fontsize_val.grid(row=1, column=2, padx=5, pady=5)

        lbl_font = ctk.CTkLabel(frame_opts, text="Font chữ:", font=ctk.CTkFont(weight="bold"))
        lbl_font.grid(row=2, column=0, padx=10, pady=5, sticky="e")
        self.font_combo = ctk.CTkOptionMenu(frame_opts, values=self.FONT_LIST, command=self.on_font_change)
        self.font_combo.set("Georgia")
        self.font_combo.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

        lbl_lang = ctk.CTkLabel(frame_opts, text="Ngôn ngữ:", font=ctk.CTkFont(weight="bold"))
        lbl_lang.grid(row=3, column=0, padx=10, pady=5, sticky="e")
        self.lang_combo = ctk.CTkOptionMenu(frame_opts, values=[
            "Vietnamese", "English", "Spanish", "Portuguese", "French",
            "German", "Italian", "Japanese", "Korean", "Chinese",
            "Hindi", "Arabic", "Russian", "Indonesian", "Thai"
        ])
        self.lang_combo.set("English")
        self.lang_combo.grid(row=3, column=1, padx=5, pady=5, sticky="ew")

        # --- KIỂU PHỤ ĐỀ ---
        lbl_style = ctk.CTkLabel(frame_opts, text="Kiểu phụ đề:", font=ctk.CTkFont(weight="bold"))
        lbl_style.grid(row=4, column=0, padx=10, pady=5, sticky="e")
        self.subtitle_style_combo = ctk.CTkOptionMenu(
            frame_opts,
            values=list(self.SUBTITLE_STYLE_MAP.keys()),
            command=lambda v: self.trigger_realtime_preview()
        )
        self.subtitle_style_combo.set("Cổ điển")
        self.subtitle_style_combo.grid(row=4, column=1, padx=5, pady=5, sticky="ew")

        # --- TÙY CHỈNH CHỮ kiểu CapCut: hoa/thường, màu chữ, màu viền, glow ---
        self.sub_case = 'keep'            # 'upper' | 'title' | 'lower' | 'keep'
        self.sub_text_color = '#FFFFFF'   # màu chữ
        self.sub_outline_color = '#000000'  # màu viền
        self.sub_glow = 0                 # 0-20 (độ tỏa sáng)

        lbl_case = ctk.CTkLabel(frame_opts, text="Hoa/thường:", font=ctk.CTkFont(weight="bold"))
        lbl_case.grid(row=5, column=0, padx=10, pady=5, sticky="e")
        self.case_seg = ctk.CTkSegmentedButton(
            frame_opts, values=["AA", "Aa", "aa", "Giữ nguyên"],
            command=self._on_sub_case_change, height=26)
        self.case_seg.set("Giữ nguyên")
        self.case_seg.grid(row=5, column=1, columnspan=2, padx=5, pady=5, sticky="ew")

        lbl_colors = ctk.CTkLabel(frame_opts, text="Màu chữ/viền:", font=ctk.CTkFont(weight="bold"))
        lbl_colors.grid(row=6, column=0, padx=10, pady=5, sticky="e")
        _color_row = ctk.CTkFrame(frame_opts, fg_color="transparent")
        _color_row.grid(row=6, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        _color_row.grid_columnconfigure((0, 1), weight=1)
        self.btn_text_color = ctk.CTkButton(_color_row, text="A Màu chữ", height=26,
                                            fg_color=self.sub_text_color, text_color="#000000",
                                            hover_color="#dddddd",
                                            command=self._pick_text_color)
        self.btn_text_color.grid(row=0, column=0, padx=(0, 3), sticky="ew")
        self.btn_outline_color = ctk.CTkButton(_color_row, text="◯ Màu viền", height=26,
                                               fg_color=self.sub_outline_color, text_color="#ffffff",
                                               hover_color="#333333",
                                               command=self._pick_outline_color)
        self.btn_outline_color.grid(row=0, column=1, padx=(3, 0), sticky="ew")

        lbl_glow = ctk.CTkLabel(frame_opts, text="Glow (tỏa sáng):", font=ctk.CTkFont(weight="bold"))
        lbl_glow.grid(row=7, column=0, padx=10, pady=5, sticky="e")
        self.slider_glow = ctk.CTkSlider(frame_opts, from_=0, to=20, number_of_steps=20,
                                         command=self._on_glow_change)
        self.slider_glow.set(0)
        self.slider_glow.grid(row=7, column=1, padx=5, pady=5, sticky="ew")
        self.lbl_glow_val = ctk.CTkLabel(frame_opts, text="0", width=30)
        self.lbl_glow_val.grid(row=7, column=2, padx=5, pady=5)

        # --- Âm lượng ---
        lbl_section_vol = ctk.CTkLabel(scroll_container, text="🔊 ÂM LƯỢNG", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        lbl_section_vol.pack(pady=(15, 5), anchor="w", padx=5)

        frame_vol = ctk.CTkFrame(scroll_container)
        frame_vol.pack(pady=5, padx=5, fill="x")
        frame_vol.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame_vol, text="Voice:", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, pady=5, sticky="e")
        self.slider_voice_vol = ctk.CTkSlider(frame_vol, from_=0, to=200, number_of_steps=200,
                                               command=lambda v: self.lbl_voice_vol.configure(text=f"{int(v)}%"))
        self.slider_voice_vol.set(100)
        self.slider_voice_vol.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.lbl_voice_vol = ctk.CTkLabel(frame_vol, text="100%", width=40)
        self.lbl_voice_vol.grid(row=0, column=2, padx=5, pady=5)

        ctk.CTkLabel(frame_vol, text="Nhạc nền:", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=10, pady=5, sticky="e")
        self.slider_bgm_vol = ctk.CTkSlider(frame_vol, from_=0, to=100, number_of_steps=100,
                                              command=lambda v: self.lbl_bgm_vol.configure(text=f"{int(v)}%"))
        self.slider_bgm_vol.set(20)
        self.slider_bgm_vol.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.lbl_bgm_vol = ctk.CTkLabel(frame_vol, text="20%", width=40)
        self.lbl_bgm_vol.grid(row=1, column=2, padx=5, pady=5)

        self.btn_mixer = ctk.CTkButton(scroll_container, text="🎧 Nghe thử & Cân bằng âm lượng",
                                        height=34, fg_color="#8e44ad", hover_color="#9b59b6",
                                        command=self.open_audio_mixer)
        self.btn_mixer.pack(pady=(5, 5), padx=10, fill="x")

        # Load default volumes
        self._load_default_volumes()

        # --- Nút hành động ---
        lbl_section_action = ctk.CTkLabel(scroll_container, text="🚀 HÀNH ĐỘNG", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
        lbl_section_action.pack(pady=(15, 5), anchor="w", padx=5)

        self.btn_add_queue = ctk.CTkButton(scroll_container, text="🚀 THÊM VÀO HÀNG ĐỢI & RENDER", font=ctk.CTkFont(size=16, weight="bold"), height=50, command=self.add_to_queue, fg_color="#27ae60")
        self.btn_add_queue.pack(pady=(5, 5), padx=10, fill="x")

        # Nút xuất Debug Bundle: nén toàn bộ session log + traceback gửi dev
        self.btn_export_debug = ctk.CTkButton(
            scroll_container, text="📤 Xuất Debug Bundle (khi có lỗi)",
            height=30, fg_color="#7f8c8d", hover_color="#95a5a6",
            font=ctk.CTkFont(size=11),
            command=self._export_debug_bundle_action
        )
        self.btn_export_debug.pack(pady=(0, 10), padx=10, fill="x")

        # ==========================================
        # PHÍA PHẢI màn DỰNG VIDEO: chỉ PREVIEW (chiếm trọn)
        # ==========================================
        frame_preview = ctk.CTkFrame(self.main_paned)
        self.main_paned.add(frame_preview, minsize=400, stretch="always")

        self.preview_container = ctk.CTkFrame(frame_preview, fg_color="black")
        self.preview_container.pack(fill="both", expand=True, padx=5, pady=5)

        # Thanh điều khiển preview (bố cục + xem trước GỘP CHUNG 1 màn hình)
        self._preview_topbar = ctk.CTkFrame(self.preview_container, fg_color="transparent")
        self._preview_topbar.pack(fill="x", padx=4, pady=(4, 0))
        ctk.CTkLabel(self._preview_topbar, text="🎬 XEM TRƯỚC + BỐ CỤC",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#48c9b0").pack(side="left", padx=(2, 6))

        # --- Điều khiển LIVE PREVIEW (phát video + xem chỉnh sửa thời gian thực) ---
        self.btn_pv_play = ctk.CTkButton(self._preview_topbar, text="▶ Phát", width=76, height=26,
                                         fg_color="#27ae60", hover_color="#2ecc71",
                                         font=ctk.CTkFont(size=12, weight="bold"),
                                         command=self._pv_toggle_play)
        self.btn_pv_play.pack(side="right", padx=(4, 0))
        ctk.CTkButton(self._preview_topbar, text="🎲", width=30, height=26, fg_color="gray30",
                      hover_color="gray40", command=self._pv_random_video).pack(side="right", padx=2)
        ctk.CTkButton(self._preview_topbar, text="🎯", width=30, height=26, fg_color="gray30",
                      hover_color="gray40", command=self._pv_pick_video).pack(side="right", padx=2)
        self.lbl_pv_src = ctk.CTkLabel(self._preview_topbar, text="(chưa chọn video preview)",
                                       text_color="gray", font=ctk.CTkFont(size=11), anchor="e")
        self.lbl_pv_src.pack(side="right", padx=6)

        # Khung preview DUY NHẤT: composer (kéo-thả) + video live làm nền
        self.preview_stack = ctk.CTkFrame(self.preview_container, fg_color="black")
        self.preview_stack.pack(fill="both", expand=True)

        # Host cho trình kéo-thả — LUÔN hiển thị (gộp với màn hình xem trước)
        self.composer_host = ctk.CTkFrame(self.preview_stack, fg_color="transparent")
        self.composer_host.pack(fill="both", expand=True)
        # Tạo composer sau khi cửa sổ đã layout xong (cần bề rộng thật)
        self.after(600, self._ensure_inline_composer)
        # Canvas preview tự co giãn theo kích thước cửa sổ (debounce)
        self._pv_resize_timer = None
        self.preview_stack.bind("<Configure>", self._on_preview_area_resize)

        # --- Trạng thái LIVE PREVIEW PLAYER ---
        self._pv_video_path = ""       # video nguồn cho preview
        self._pv_playing = False
        self._pv_stop_flag = False
        self._pv_reopen = False
        self._pv_step_once = False
        self._pv_thread = None
        self._pv_last_frame = None     # frame BGR gần nhất (đã thu nhỏ)
        self._pv_image_frame = None    # ảnh nền BGR (mode Tạo từ Ảnh)
        self._pv_ov_path = ""          # overlay cụ thể đang áp lên preview
        self._pv_ov_reopen = False
        self._pv_ov_frame = None       # frame overlay BGR gần nhất
        self._pv_sub_overlay = None    # lớp phụ đề ASS trong suốt (PIL RGBA — frame fallback)
        self._pv_sub_frames = None     # chuỗi frame phụ đề ĐỘNG (loop 5s theo kiểu đang chọn)
        self._pv_clock = 0.0           # đồng hồ phát của preview (giây)
        self._pv_sub_building = False
        self._pv_sub_dirty = False
        self._pv_busy = False
        self._pv_res = (960, 540)      # độ phân giải dựng preview nội bộ
        # Tự nạp 1 video nền random để màn hình preview không trống
        self.after(1200, self._pv_autoload)

        # ==========================================
        # MÀN HÌNH 2: 📊 XUẤT VIDEO & HÀNG CHỜ
        #   Trái: video đang xuất (tiến độ + monitor + log) · Phải: hàng chờ
        # ==========================================
        out_paned = tk.PanedWindow(
            self.tab_output, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.RAISED,
            bg="#2b2b2b", borderwidth=0
        )
        out_paned.pack(fill="both", expand=True)

        # --- Trái: VIDEO ĐANG XUẤT ---
        frame_current = ctk.CTkFrame(out_paned)
        out_paned.add(frame_current, minsize=420, stretch="always")

        ctk.CTkLabel(frame_current, text="🎬 VIDEO ĐANG XUẤT",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#3498db").pack(anchor="w", padx=10, pady=(8, 4))

        self.progress_frame = ctk.CTkFrame(frame_current, fg_color="transparent")
        self.progress_frame.pack(fill="x", padx=10, pady=(0, 3))
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame)
        self.progress_bar.pack(side="left", padx=(0, 10), expand=True, fill="x")
        self.progress_bar.set(0)
        self.lbl_percent = ctk.CTkLabel(self.progress_frame, text="0%", font=ctk.CTkFont(weight="bold"))
        self.lbl_percent.pack(side="right")

        self.lbl_live_progress = ctk.CTkLabel(frame_current, text="Sẵn sàng...", text_color="#f39c12", font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_live_progress.pack(pady=(0, 3))

        # --- MONITOR chi tiết stage — hiện khi đang render ---
        self.render_monitor_frame = ctk.CTkFrame(frame_current, fg_color="#1a1a1a",
                                                  corner_radius=8)
        self.lbl_render_monitor = ctk.CTkLabel(
            self.render_monitor_frame, text="", justify="left", anchor="w",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color="#f0f0f0")
        self.lbl_render_monitor.pack(fill="x", padx=10, pady=6)
        # Mặc định ẩn — chỉ pack khi bắt đầu render
        self.render_monitor_visible = False

        # Nút hủy render — ẩn khi chưa render
        self.btn_cancel_render = ctk.CTkButton(
            frame_current, text="⛔ HỦY VIDEO ĐANG RENDER", height=36,
            fg_color="#c0392b", hover_color="#e74c3c",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.cancel_current_render
        )
        self.btn_cancel_render.pack(fill="x", padx=10, pady=(0, 5))
        self.btn_cancel_render.pack_forget()

        ctk.CTkLabel(frame_current, text="📝 LOG TIẾN ĐỘ",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#3498db").pack(anchor="w", padx=10, pady=(6, 2))
        self.log_box = ctk.CTkTextbox(frame_current, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # --- Phải: HÀNG CHỜ ---
        frame_qside = ctk.CTkFrame(out_paned)
        out_paned.add(frame_qside, minsize=340, width=560, stretch="never")

        ctk.CTkLabel(frame_qside, text="⏳ HÀNG CHỜ",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f39c12").pack(anchor="w", padx=10, pady=(8, 4))
        self.queue_scroll = ctk.CTkScrollableFrame(frame_qside, fg_color="transparent")
        self.queue_scroll.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        self.update_queue_ui()

    def get_resolution(self, ratio_string):
        mapping = {
            "16:9 (YouTube/Ngang)": (1920, 1080),
            "9:16 (TikTok/Shorts)": (1080, 1920),
            "4:3 (Classic)": (1440, 1080),
            "3:4 (Instagram)": (1080, 1440),
            "1:1 (Vuông)": (1080, 1080)
        }
        return mapping.get(ratio_string, (1920, 1080)) 

    # ==========================================
    # STARTUP & UPDATE
    # ==========================================
    def _startup_checks(self):
        """Kiểm tra hệ thống khi khởi động."""
        # Dọn file tạm từ lần chạy trước (nếu crash)
        self.cleanup_temp_files()
        
        # Check FFmpeg
        if FFMPEG_DIR is False:
            self.log("⚠️ CẢNH BÁO: Không tìm thấy FFmpeg!")
            self.log("   Đặt ffmpeg.exe vào thư mục 'ffmpeg/' cạnh ứng dụng")
            self.log(f"   Hoặc cài FFmpeg vào PATH hệ thống")
            messagebox.showwarning("Thiếu FFmpeg", 
                "Không tìm thấy FFmpeg!\n\n"
                "Cách 1: Tạo thư mục 'ffmpeg' cạnh ứng dụng,\n"
                "  đặt ffmpeg.exe, ffprobe.exe, ffplay.exe vào đó.\n\n"
                "Cách 2: Cài FFmpeg vào PATH hệ thống.\n\n"
                "Tải FFmpeg: https://www.gyan.dev/ffmpeg/builds/")
        else:
            ff = get_ffmpeg('ffmpeg')
            self.log(f"✅ FFmpeg: {ff}")
        
        # Check folders quan trọng tồn tại
        for fname, desc in [('bgm', 'Nhạc nền')]:
            fpath = os.path.join(APP_DIR, fname)
            if not os.path.exists(fpath):
                try:
                    os.makedirs(fpath, exist_ok=True)
                    self.log(f"✅ Đã tạo thư mục {desc}: {fpath}")
                except Exception:
                    self.log(f"⚠️ Không tạo được {fpath}")
        
        # Check layout_composer.py có ĐÚNG BỘ với app.py không (copy tay hay bị sót file)
        if COMPOSER_AVAILABLE and _COMPOSER_VER != REQUIRED_COMPOSER_VERSION:
            self.log(f"⚠ layout_composer.py là BẢN CŨ ({_COMPOSER_VER}) — cần bản {REQUIRED_COMPOSER_VERSION}!")
            self.after(800, lambda: messagebox.showwarning(
                "File không đồng bộ",
                f"app.py là v{APP_VERSION} nhưng layout_composer.py là bản CŨ.\n\n"
                "Đây là lý do các tính năng mới của trình kéo-thả\n"
                "(8 ô chỉnh kích thước, thêm không tách nền...) không xuất hiện.\n\n"
                "→ Copy đè file layout_composer.py MỚI từ máy chính sang\n"
                "   (hoặc copy cả gói từ 1_DONG_GOI_MANG_DI.bat)."))

        # Check for updates (background)
        threading.Thread(target=self._bg_check_update, daemon=True).start()

    @staticmethod
    def _ver_tuple(v):
        """'1.7.0' → (1,7,0) để so sánh đúng (tránh '1.10' < '1.9' kiểu chuỗi)."""
        try:
            return tuple(int(x) for x in str(v).strip().lstrip('v').split('.'))
        except Exception:
            return (0,)

    def _open_url(self, url, timeout=10):
        """Mở URL với xác thực SSL đáng tin cậy.
        Máy Windows cũ hay thiếu chứng chỉ gốc → CERTIFICATE_VERIFY_FAILED.
        Thứ tự thử: (0) bỏ xác thực nếu user đã đồng ý → (1) bộ cert certifi
        đi kèm venv → (2) cert hệ thống Windows."""
        import urllib.request, urllib.error, ssl
        req = urllib.request.Request(url, headers={'User-Agent': 'VanPham-App'})
        ctxs = []
        if getattr(self, '_ssl_skip_verify', False):
            _u = ssl.create_default_context()
            _u.check_hostname = False
            _u.verify_mode = ssl.CERT_NONE
            ctxs.append(_u)
        try:
            import certifi
            ctxs.append(ssl.create_default_context(cafile=certifi.where()))
        except Exception:
            pass
        ctxs.append(ssl.create_default_context())
        last_err = None
        for ctx in ctxs:
            try:
                return urllib.request.urlopen(req, timeout=timeout, context=ctx)
            except (ssl.SSLError, urllib.error.URLError) as e:
                _reason = getattr(e, 'reason', e)
                if isinstance(e, ssl.SSLError) or isinstance(_reason, ssl.SSLError):
                    last_err = e
                    continue
                raise
        raise last_err

    def _fetch_latest_release(self, timeout=10):
        """Gọi GitHub API lấy release mới nhất. Trả về dict hoặc raise."""
        import json
        with self._open_url(UPDATE_URL, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    # ============================================================
    # BÁO LỖI TỪ XA — mọi máy gặp lỗi tự gửi báo cáo về Discord của dev
    # ============================================================
    def _on_tk_exception(self, exc, val, tb):
        """Handler cho mọi lỗi callback Tkinter (nút bấm, sự kiện...)."""
        import traceback as _tb
        tb_text = "".join(_tb.format_exception(exc, val, tb))
        try:
            self.log(f"❌ Lỗi: {getattr(exc, '__name__', exc)}: {val}")
        except Exception:
            pass
        self._report_error_remote(f"{getattr(exc, '__name__', 'Error')}: {val}",
                                  str(val), tb_text)

    def _report_error_remote(self, title, err_text, tb_text="", context=""):
        """Gửi báo cáo lỗi về Discord webhook (chạy nền, không chặn UI).
        Tự bỏ qua nếu: chưa cấu hình webhook, hoặc máy có file DEV_MACHINE_MARKER,
        hoặc lỗi trùng đã gửi trong phiên (chống spam)."""
        try:
            if not DISCORD_WEBHOOK_URL or 'discord.com' not in DISCORD_WEBHOOK_URL:
                return
            if os.path.exists(os.path.join(APP_DIR, DEV_MACHINE_MARKER)):
                return
            # Chống spam: mỗi chữ ký lỗi chỉ gửi 1 lần/phiên, tối đa 8 lỗi/phiên
            sig = f"{title}|{err_text}"[:200]
            sent = getattr(self, '_err_sent', None)
            if sent is None:
                sent = self._err_sent = set()
            if sig in sent or len(sent) >= 8:
                return
            sent.add(sig)
            threading.Thread(target=self._do_report_error_remote,
                             args=(title, err_text, tb_text, context), daemon=True).start()
        except Exception:
            pass

    def _do_report_error_remote(self, title, err_text, tb_text, context):
        import json, platform, socket, datetime, urllib.request, ssl
        try:
            try: machine = socket.gethostname()
            except Exception: machine = "?"
            when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            osname = f"{platform.system()} {platform.release()}"
            # Nội dung gọn (Discord tối đa 2000 ký tự/tin) — ưu tiên phần đuôi
            # traceback vì đó là dòng lỗi thật.
            body = (
                f"🔴 **LỖI VẠN PHẨM** v{APP_VERSION}\n"
                f"🖥 Máy: `{machine}`  |  {osname}  |  {when}\n"
                f"⚠ **{title}**\n"
                f"```\n{err_text[:300]}\n```"
            )
            tb = (tb_text or "").strip()
            if tb:
                _room = 1850 - len(body)
                if _room > 200:
                    body += f"```\n{tb[-_room:]}\n```"
            if context:
                body = body[:1900] + f"\n📌 {context[:80]}"
            data = json.dumps({"content": body[:1990],
                               "username": f"VanPham v{APP_VERSION}"}).encode('utf-8')
            req = urllib.request.Request(
                DISCORD_WEBHOOK_URL, data=data,
                headers={'Content-Type': 'application/json', 'User-Agent': 'VanPham-App'})
            # Dùng cùng cách xác thực SSL bền như _open_url
            ctxs = []
            try:
                import certifi
                ctxs.append(ssl.create_default_context(cafile=certifi.where()))
            except Exception:
                pass
            ctxs.append(ssl.create_default_context())
            for ctx in ctxs:
                try:
                    urllib.request.urlopen(req, timeout=15, context=ctx)
                    break
                except ssl.SSLError:
                    continue
                except Exception:
                    break
        except Exception:
            pass

    def _bg_check_update(self):
        """Background check for updates on startup."""
        if 'YOUR_USER' in GITHUB_REPO:
            return  # chưa cấu hình repo → bỏ qua
        try:
            data = self._fetch_latest_release(timeout=5)
            latest = data.get('tag_name', '').lstrip('v')
            if latest and self._ver_tuple(latest) > self._ver_tuple(APP_VERSION):
                self.after(0, lambda: self._show_update_available(
                    latest, data.get('zipball_url', ''), data.get('body', '')))
        except Exception:
            pass  # Không internet hoặc lỗi → bỏ qua

    def _show_update_available(self, version, zip_url, notes):
        """Hiện thông báo có bản cập nhật (chấm đỏ trên nút 🔄)."""
        self.btn_update.configure(text="🔴", text_color="#e74c3c", fg_color="#3a1010", hover_color="#5a1010")
        self.lbl_version.configure(text=f"v{APP_VERSION} → v{version}", text_color="#e74c3c")
        self._pending_update = {'version': version, 'zip_url': zip_url, 'notes': notes}

    def check_for_updates(self):
        """User bấm nút 🔄: kiểm tra + đề nghị TỰ CẬP NHẬT (tải, thay code, khởi động lại)."""
        if 'YOUR_USER' in GITHUB_REPO:
            messagebox.showinfo(
                "Chưa cấu hình cập nhật",
                "Tính năng tự cập nhật cần 1 repo GitHub.\n\n"
                "Mở file app.py, sửa dòng gần đầu file:\n"
                '    GITHUB_REPO = "YOUR_USER/YOUR_REPO"\n'
                "thành repo của bạn (vd \"minhchinh/van-pham\").\n\n"
                "Xem hướng dẫn đầy đủ trong file HUONG_DAN_CAP_NHAT_GITHUB.txt")
            return
        if getattr(self, '_pending_update', None):
            upd = self._pending_update
            if self.is_rendering:
                messagebox.showwarning("Đang render",
                    "Đang render video — hãy cập nhật sau khi render xong.")
                return
            result = messagebox.askyesno("Cập nhật mới",
                f"🆕 Phiên bản mới: v{upd['version']}\n"
                f"Phiên bản hiện tại: v{APP_VERSION}\n\n"
                f"Nội dung cập nhật:\n{(upd['notes'] or '')[:300]}\n\n"
                f"Cập nhật NGAY? (tool tự tải + thay code + khởi động lại.\n"
                f"Cài đặt, video, nhạc, model... trên máy này GIỮ NGUYÊN.)")
            if result and upd['zip_url']:
                self.btn_update.configure(text="⏳")
                threading.Thread(target=self._do_self_update,
                                 args=(upd['version'], upd['zip_url']), daemon=True).start()
        else:
            self.btn_update.configure(text="⏳", text_color="gray")
            def _check():
                try:
                    data = self._fetch_latest_release(timeout=10)
                    latest = data.get('tag_name', '').lstrip('v')
                    if latest and self._ver_tuple(latest) > self._ver_tuple(APP_VERSION):
                        self.after(0, lambda: self._show_update_available(
                            latest, data.get('zipball_url', ''), data.get('body', '')))
                        self.after(0, self.check_for_updates)
                    else:
                        self.after(0, lambda: (
                            self.btn_update.configure(text="✅", text_color="#27ae60"),
                            messagebox.showinfo("Cập nhật", f"Bạn đang dùng phiên bản mới nhất (v{APP_VERSION})!")
                        ))
                except Exception as e:
                    # PHẢI chốt lỗi vào biến mặc định của lambda: Python xóa `e`
                    # khi hết except → lambda chạy sau sẽ NameError im lặng.
                    _msg = f"{type(e).__name__}: {e}"
                    if 'CERTIFICATE_VERIFY_FAILED' in _msg:
                        def _ask_skip_ssl():
                            self.btn_update.configure(text="🔄", text_color="gray")
                            if messagebox.askyesno("Lỗi chứng chỉ SSL",
                                    "Máy này không xác thực được chứng chỉ SSL của GitHub\n"
                                    "(Windows thiếu/cũ chứng chỉ gốc, hoặc diệt virus chen vào).\n\n"
                                    "CÁCH CHỮA TỐT NHẤT (an toàn):\n"
                                    "  • Chạy Windows Update rồi khởi động lại máy, HOẶC\n"
                                    "  • Chạy lại 2_CAI_DAT_MAY_MOI.bat để cài thêm certifi\n\n"
                                    "Hoặc TẠM THỜI bỏ qua xác thực SSL và thử lại ngay?\n"
                                    "(kém an toàn hơn — chỉ áp dụng cho phiên này)"):
                                self._ssl_skip_verify = True
                                self.check_for_updates()
                        self.after(0, _ask_skip_ssl)
                        return
                    self.after(0, lambda m=_msg: (
                        self.btn_update.configure(text="🔄", text_color="gray"),
                        messagebox.showwarning("Lỗi",
                            f"Không thể kiểm tra cập nhật.\n{m}\n\n"
                            f"Máy này có thể không ra được internet tới github.com\n"
                            f"(kiểm tra mạng / tường lửa / phần mềm diệt virus).")
                    ))
            threading.Thread(target=_check, daemon=True).start()

    def _do_self_update(self, version, zip_url):
        """TỰ CẬP NHẬT (chạy nền):
        1. Tải zip source của release (zipball — GitHub tự tạo, không cần upload file)
        2. Giải nén vào thư mục tạm
        3. Backup file code cũ vào backup_update\\v<cũ>\\ rồi thay bằng file mới
           (CHỈ thay file trong UPDATE_CODE_FILES — venv/cài đặt/dữ liệu giữ nguyên)
        4. requirements.txt đổi → tự pip install thư viện mới
        5. Khởi động lại tool"""
        import zipfile, shutil, re as _re
        try:
            self.log(f"🔄 Đang tải bản cập nhật v{version}...")
            zpath = os.path.join(TEMP_DIR, f"update_v{version}.zip")
            with self._open_url(zip_url, timeout=120) as resp, open(zpath, 'wb') as f:
                shutil.copyfileobj(resp, f)

            exdir = os.path.join(TEMP_DIR, f"update_v{version}")
            if os.path.isdir(exdir):
                shutil.rmtree(exdir, ignore_errors=True)
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(exdir)
            # Zipball có đúng 1 thư mục gốc user-repo-hash/
            roots = [os.path.join(exdir, d) for d in os.listdir(exdir)
                     if os.path.isdir(os.path.join(exdir, d))]
            src_root = roots[0] if roots else exdir
            if not os.path.isfile(os.path.join(src_root, 'app.py')):
                raise RuntimeError("Gói cập nhật không có app.py — kiểm tra lại repo/release.")

            # ===== KHÓA CHỐNG HẠ CẤP =====
            # Đọc APP_VERSION THẬT trong app.py của gói tải về. Nếu KHÔNG mới hơn
            # bản đang chạy → TỪ CHỐI thay (tránh tai nạn: GitHub lỡ còn code cũ,
            # tag mới nhưng file cũ → tự cập nhật ngược phá mất bản mới trên máy).
            try:
                with open(os.path.join(src_root, 'app.py'), encoding='utf-8') as _af:
                    _head = _af.read(4000)
                _m = _re.search(r'APP_VERSION\s*=\s*["\']([\d.]+)["\']', _head)
                _pkg_ver = _m.group(1) if _m else '0'
            except Exception:
                _pkg_ver = '0'
            if self._ver_tuple(_pkg_ver) <= self._ver_tuple(APP_VERSION):
                shutil.rmtree(exdir, ignore_errors=True)
                try: os.remove(zpath)
                except Exception: pass
                _cur = APP_VERSION
                self.after(0, lambda pv=_pkg_ver, cur=_cur: (
                    self.btn_update.configure(text="🔄", text_color="gray"),
                    messagebox.showwarning("Bỏ qua cập nhật",
                        f"Gói trên GitHub là code v{pv}, KHÔNG mới hơn bản đang chạy v{cur}.\n\n"
                        f"Đã BỎ QUA để không ghi đè bản mới bằng bản cũ.\n\n"
                        f"Nếu bạn vừa sửa code: hãy UPLOAD file mới lên GitHub TRƯỚC,\n"
                        f"rồi mới tạo Release/tag (tag tạo trước khi upload sẽ chứa code cũ).")))
                self.log(f"⛔ Bỏ qua cập nhật: gói v{_pkg_ver} không mới hơn v{APP_VERSION}.")
                return

            # So sánh requirements trước khi ghi đè
            req_old = req_new = ''
            try:
                with open(os.path.join(APP_DIR, 'requirements.txt'), encoding='utf-8') as f:
                    req_old = f.read()
                with open(os.path.join(src_root, 'requirements.txt'), encoding='utf-8') as f:
                    req_new = f.read()
            except Exception:
                pass

            # Backup + thay file code
            bdir = os.path.join(APP_DIR, 'backup_update', f'v{APP_VERSION}')
            os.makedirs(bdir, exist_ok=True)
            replaced = []
            for fn in UPDATE_CODE_FILES:
                sf = os.path.join(src_root, fn)
                if not os.path.isfile(sf):
                    continue
                df = os.path.join(APP_DIR, fn)
                try:
                    if os.path.isfile(df):
                        shutil.copy2(df, os.path.join(bdir, fn))
                    shutil.copy2(sf, df)
                    replaced.append(fn)
                except Exception as e:
                    self.log(f"⚠ Không thay được {fn}: {e}")
            self.log(f"✅ Đã cập nhật {len(replaced)} file: {', '.join(replaced)}")
            self.log(f"   (Bản cũ backup tại backup_update\\v{APP_VERSION})")

            # Thư viện mới (nếu requirements đổi) — nhưng KHÔNG ĐỤNG TỚI torch:
            # torch cài qua requirements sẽ là bản CPU → phân tích kịch bản chậm.
            # Giữ nguyên torch đang có (thường là bản GPU user tự cài), chỉ cài các
            # gói KHÁC còn thiếu/đổi.
            if req_new and req_new != req_old:
                self.log("📦 requirements.txt thay đổi → cài thư viện mới (BỎ QUA torch để giữ bản GPU)...")
                _req_lines = [ln for ln in req_new.splitlines()
                              if ln.strip() and not ln.strip().startswith('#')
                              and not ln.strip().lower().startswith(('torch', 'torchvision', 'torchaudio'))]
                _tmp_req = os.path.join(TEMP_DIR, "update_requirements_notorch.txt")
                try:
                    with open(_tmp_req, 'w', encoding='utf-8') as _rf:
                        _rf.write("\n".join(_req_lines))
                    r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', _tmp_req],
                                       capture_output=True, text=True, creationflags=0x08000000)
                    if r.returncode == 0:
                        self.log("✅ Đã cài xong thư viện mới (torch giữ nguyên).")
                    else:
                        self.log("⚠ Cài thư viện lỗi — hãy chạy lại 2_CAI_DAT_MAY_MOI.bat sau khi khởi động lại.")
                    try: os.remove(_tmp_req)
                    except Exception: pass
                except Exception as _re2:
                    self.log(f"⚠ Không cài được thư viện mới: {_re2}")

            # Dọn tạm
            try:
                os.remove(zpath); shutil.rmtree(exdir, ignore_errors=True)
            except Exception:
                pass

            def _restart():
                if messagebox.askyesno("Cập nhật xong",
                        f"✅ Đã cập nhật lên v{version}!\n\nKhởi động lại tool ngay?"):
                    try:
                        subprocess.Popen([sys.executable, os.path.join(APP_DIR, 'app.py')],
                                         cwd=APP_DIR)
                    except Exception:
                        pass
                    try:
                        self._save_user_settings()
                    except Exception:
                        pass
                    os._exit(0)   # thoát hẳn để bản mới tiếp quản
                else:
                    self.lbl_version.configure(text=f"v{version} (khởi động lại để áp dụng)",
                                               text_color="#27ae60")
                    self.btn_update.configure(text="✅", text_color="#27ae60")
            self.after(0, _restart)
        except Exception as e:
            self.log(f"❌ Cập nhật thất bại: {e}")
            _msg = f"{type(e).__name__}: {e}"   # chốt lỗi trước khi hết khối except
            self.after(0, lambda m=_msg: (
                self.btn_update.configure(text="🔴", text_color="#e74c3c"),
                messagebox.showwarning("Lỗi cập nhật",
                    f"Không cập nhật được:\n{m}\n\nTool vẫn chạy bản hiện tại bình thường.")))

    # ============================================================
    # LƯU / KHÔI PHỤC CÀI ĐẶT NGƯỜI DÙNG (user_settings.json)
    # Tự động lưu mỗi khi có thay đổi → mở tool lần sau y chang phiên trước.
    # ============================================================
    SETTINGS_FILE = os.path.join(APP_DIR, "user_settings.json")

    def _collect_settings(self):
        """Gom toàn bộ cài đặt hiện tại thành dict (JSON-safe)."""
        try:
            return {
                # Chế độ + thư mục hay dùng
                'render_mode': self.render_mode,
                'bg_video_folder': self.bg_video_folder,
                'img_folder': self.img_folder,
                'output_folder': self.output_folder,
                'overlay_specific_file': self.overlay_specific_file,
                # Chỉnh nền video
                'bg_zoom': self.bg_zoom, 'bg_off_x': self.bg_off_x, 'bg_off_y': self.bg_off_y,
                'bg_brightness': self.bg_brightness, 'bg_contrast': self.bg_contrast,
                'bg_saturation': self.bg_saturation, 'bg_effect': self.bg_effect,
                'xfade_on': self.jesus_xfade_dur > 0,
                # Phụ đề
                'outline': float(self.slider_outline.get()),
                'fontsize': int(self.slider_fontsize.get()),
                'font': self.font_combo.get(),
                'language': self.lang_combo.get(),
                'subtitle_style': self.subtitle_style_combo.get(),
                'sub_case': self.sub_case,
                'jesus_key_fine': self.jesus_key_fine,
                'sub_text_color': self.sub_text_color,
                'sub_outline_color': self.sub_outline_color,
                'sub_glow': self.sub_glow,
                # Âm lượng
                'voice_vol': int(self.slider_voice_vol.get()),
                'bgm_vol': int(self.slider_bgm_vol.get()),
                # Bố cục kéo-thả
                'overlay_layers': self.overlay_layers,
                'kb_text_layout': self.kb_text_layout,
            }
        except Exception:
            return None

    def _save_user_settings(self):
        cur = self._collect_settings()
        if cur is None:
            return
        try:
            with open(self.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(cur, f, ensure_ascii=False, indent=1)
            self._last_saved_settings = json.dumps(cur, sort_keys=True, ensure_ascii=False)
        except Exception:
            pass

    def _settings_autosave_tick(self):
        """Mỗi 4s: có thay đổi so với lần lưu trước → ghi file. Nhẹ, không chặn UI."""
        try:
            cur = self._collect_settings()
            if cur is not None:
                sig = json.dumps(cur, sort_keys=True, ensure_ascii=False)
                if sig != getattr(self, '_last_saved_settings', None):
                    with open(self.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                        f.write(json.dumps(cur, ensure_ascii=False, indent=1))
                    self._last_saved_settings = sig
        except Exception:
            pass
        self.after(4000, self._settings_autosave_tick)

    def _load_user_settings(self):
        """Khôi phục cài đặt phiên trước vào biến + widget."""
        try:
            if not os.path.exists(self.SETTINGS_FILE):
                return
            with open(self.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            return
        try:
            # --- Chỉnh nền video ---
            self.slider_bg_zoom.set(int(d.get('bg_zoom', self.bg_zoom)));   self._on_bg_zoom(self.slider_bg_zoom.get())
            self.slider_bg_off_x.set(int(d.get('bg_off_x', self.bg_off_x))); self._on_bg_off_x(self.slider_bg_off_x.get())
            self.slider_bg_off_y.set(int(d.get('bg_off_y', self.bg_off_y))); self._on_bg_off_y(self.slider_bg_off_y.get())
            self.slider_bg_bright.set(int(d.get('bg_brightness', self.bg_brightness))); self._on_bg_bright(self.slider_bg_bright.get())
            self.slider_bg_contrast.set(int(d.get('bg_contrast', self.bg_contrast)));   self._on_bg_contrast(self.slider_bg_contrast.get())
            self.slider_bg_sat.set(int(d.get('bg_saturation', self.bg_saturation)));    self._on_bg_sat(self.slider_bg_sat.get())
            self.bg_effect = d.get('bg_effect', 'none')
            _fx_inv = {v: k for k, v in self.BG_EFFECT_MAP.items()}
            self.bg_effect_combo.set(_fx_inv.get(self.bg_effect, "Không"))
            _xf_on = bool(d.get('xfade_on', True))
            self.jesus_xfade_dur = 0.5 if _xf_on else 0.0
            (self.sw_xfade.select if _xf_on else self.sw_xfade.deselect)()
            # --- Phụ đề ---
            self.slider_outline.set(float(d.get('outline', 4.0)))
            self.lbl_outline_val.configure(text=f"{float(d.get('outline', 4.0)):.1f}")
            self.slider_fontsize.set(int(d.get('fontsize', 55)))
            self.lbl_fontsize_val.configure(text=f"{int(d.get('fontsize', 55))}")
            self.font_combo.set(d.get('font', 'Georgia'))
            self.lang_combo.set(d.get('language', 'English'))
            self.subtitle_style_combo.set(d.get('subtitle_style', 'Cổ điển'))
            self.sub_case = d.get('sub_case', 'keep')
            self.jesus_key_fine = bool(d.get('jesus_key_fine', False))
            _case_inv = {'upper': 'AA', 'title': 'Aa', 'lower': 'aa'}
            self.case_seg.set(_case_inv.get(self.sub_case, "Giữ nguyên"))
            self.sub_text_color = d.get('sub_text_color', '#FFFFFF')
            self.sub_outline_color = d.get('sub_outline_color', '#000000')
            self.btn_text_color.configure(fg_color=self.sub_text_color)
            self.btn_outline_color.configure(fg_color=self.sub_outline_color)
            self.sub_glow = int(d.get('sub_glow', 0))
            self.slider_glow.set(self.sub_glow)
            self.lbl_glow_val.configure(text=str(self.sub_glow))
            # --- Âm lượng ---
            self.slider_voice_vol.set(int(d.get('voice_vol', 100)))
            self.lbl_voice_vol.configure(text=f"{int(d.get('voice_vol', 100))}%")
            self.slider_bgm_vol.set(int(d.get('bgm_vol', 20)))
            self.lbl_bgm_vol.configure(text=f"{int(d.get('bgm_vol', 20))}%")
            # --- Thư mục + overlay cụ thể (chỉ nhận nếu còn tồn tại) ---
            _bgf = d.get('bg_video_folder', '')
            if _bgf and os.path.isdir(_bgf):
                self.bg_video_folder = _bgf
            _imf = d.get('img_folder', '')
            if _imf and os.path.isdir(_imf):
                self.img_folder = _imf
            _outf = d.get('output_folder', '')
            if _outf and os.path.isdir(_outf):
                self.output_folder = _outf
                self.lbl_out.configure(text=os.path.basename(_outf), text_color="white")
            _ovf = d.get('overlay_specific_file', '')
            if _ovf and os.path.isfile(_ovf):
                self.overlay_specific_file = _ovf
                self.lbl_overlay_pick.configure(text="🎯 " + os.path.basename(_ovf),
                                                text_color="#48c9b0")
                self._pv_ov_path = _ovf
                self._pv_ov_reopen = True
            # --- Bố cục kéo-thả ---
            if isinstance(d.get('overlay_layers'), list):
                self.overlay_layers = d['overlay_layers']
            if isinstance(d.get('kb_text_layout'), dict):
                self.kb_text_layout.update(d['kb_text_layout'])
            # --- Chế độ render (áp cuối cùng để UI đổi theo) ---
            _mode = d.get('render_mode', 'ken_burns')
            _label = "🎬 Tạo từ Video nền" if _mode == 'jesus_split' else "🖼️ Tạo từ Ảnh"
            self.render_mode_seg.set(_label)
            self._on_render_mode_change(_label)
            if self.inline_composer is not None:
                self.inline_composer.render_all()
            self.log("💾 Đã khôi phục cài đặt phiên trước (user_settings.json).")
            self._last_saved_settings = json.dumps(self._collect_settings(),
                                                   sort_keys=True, ensure_ascii=False)
        except Exception as e:
            self.log(f"⚠ Không khôi phục được 1 phần cài đặt: {e}")

    def on_closing(self):
        self._save_user_settings()   # chốt cài đặt lần cuối trước khi thoát
        if self.is_rendering:
            if messagebox.askokcancel("Cảnh báo", "🚨 CẢNH BÁO: Tool đang render!\nBạn có chắc chắn muốn hủy và thoát không?"):
                self.cancel_render = True 
                self.kill_all_processes()
                self.cleanup_temp_files()
                self.destroy()
                os._exit(0) 
        else:
            self.cleanup_temp_files()
            self.destroy()
            os._exit(0)

    def kill_all_processes(self):
        for p in self.active_processes:
            try: p.kill()
            except: pass
        self.active_processes.clear()

    def cleanup_temp_files(self):
        """Dọn tất cả file tạm trong TEMP_DIR."""
        try:
            if os.path.exists(TEMP_DIR):
                for f in os.listdir(TEMP_DIR):
                    fp = os.path.join(TEMP_DIR, f)
                    try:
                        if os.path.isfile(fp): os.remove(fp)
                    except: pass
        except: pass

    def _cleanup_task_temp(self, task):
        """Xóa NGAY các file tạm của RIÊNG video vừa render xong (nhận diện theo
        mã hash của file voice trong tên file). Không đụng tới file tạm của video
        kế tiếp đang được pre-process nền, cũng không đụng file preview."""
        try:
            vf = (task or {}).get('voice_file', '')
            if not vf:
                return
            tags = [
                hashlib.md5(vf.encode()).hexdigest()[:8],            # main_/jmain_/bgpre_/bglist_/bgmerged_/jesus_fc_/jesus_cidfc_
                hashlib.md5((vf + 'par').encode()).hexdigest()[:8],  # seg_* (Pass 2 song song)
            ]
            n, sz = 0, 0
            if os.path.exists(TEMP_DIR):
                for f in os.listdir(TEMP_DIR):
                    if any(t in f for t in tags):
                        fp = os.path.join(TEMP_DIR, f)
                        try:
                            if os.path.isfile(fp):
                                sz += os.path.getsize(fp)
                                os.remove(fp)
                                n += 1
                        except Exception:
                            pass  # file đang bị FFmpeg giữ (vd vừa hủy) → dọn lúc thoát app
            if n:
                self.log(f"🧹 Đã dọn {n} file tạm của video này (~{sz/1048576:.0f} MB, ổ C nhẹ lại)")
        except Exception:
            pass

    def cancel_current_render(self):
        """Hủy video đang render, tiếp tục video tiếp theo nếu còn."""
        if not self.is_rendering:
            return
        action = messagebox.askyesnocancel(
            "Hủy render",
            "⛔ Bạn muốn hủy video đang render.\n\n"
            "• [Yes] → Hủy video này, render video tiếp theo\n"
            "• [No] → Hủy toàn bộ hàng đợi\n"
            "• [Cancel] → Không hủy, tiếp tục render"
        )
        if action is None:  # Cancel — không hủy
            return
        
        self.cancel_render = True
        self.kill_all_processes()
        
        if action:  # Yes — hủy video này, tiếp tục queue
            self.log("⛔ Đã hủy video đang render. Chuyển sang video tiếp theo...")
            # Giữ cancel_render = True để finally block không gọi start_next_task
            # Sau 1.5s (chờ thread render dọn xong), reset và chạy video tiếp
            def _resume_queue():
                self.cancel_render = False
                self.start_next_task()
            self.after(1500, _resume_queue)
        else:  # No — hủy toàn bộ
            self.log("⛔ Đã hủy toàn bộ hàng đợi!")
            self.render_queue.clear()
            self._preprocess_result.clear()
            self.is_rendering = False
            self.btn_cancel_render.pack_forget()
            self.update_progress_ui(0.0, "Đã hủy")
            self._set_render_status("⛔ ĐÃ HỦY RENDER", "#8B0000")
            self.update_queue_ui()

    # ==========================================
    # ⚡ SPEED PANEL — Pre-process Overlay
    # ==========================================
    def _is_overlay_optimized(self, overlay_path):
        """Kiểm tra overlay đã được tối ưu chưa (đúng 1920x1080)."""
        try:
            r = subprocess.run([get_ffmpeg('ffprobe'), '-v', 'quiet', '-select_streams', 'v:0',
                               '-show_entries', 'stream=width,height', '-of', 'csv=p=0', overlay_path],
                              capture_output=True, text=True, creationflags=0x08000000)
            parts = r.stdout.strip().split(',')
            if len(parts) >= 2:
                w, h = int(parts[0]), int(parts[1])
                return w == 1920 and h == 1080
        except Exception:
            pass
        return False

    def _count_overlay_status(self):
        """Đếm overlay đã/chưa tối ưu."""
        if not self.overlay_folder or not os.path.exists(self.overlay_folder):
            return 0, 0
        valid = ('.mp4', '.mov', '.avi', '.mkv')
        overlays = [os.path.join(self.overlay_folder, f) for f in os.listdir(self.overlay_folder) if f.lower().endswith(valid)]
        done = sum(1 for f in overlays if self._is_overlay_optimized(f))
        return done, len(overlays)

    def open_speed_panel(self):
        """Mở popup panel tối ưu tốc độ."""
        win = ctk.CTkToplevel(self)
        win.title("⚡ Tối ưu tốc độ Render")
        win.geometry("500x380")
        win.resizable(False, False)
        win.grab_set()
        win.focus_force()
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 500) // 2
        y = self.winfo_y() + (self.winfo_height() - 380) // 2
        win.geometry(f"+{x}+{y}")
        
        # Header
        ctk.CTkLabel(win, text="⚡ TỐI ƯU TỐC ĐỘ RENDER", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#f39c12").pack(pady=(15, 5))
        
        # --- Overlay section ---
        ovl_frame = ctk.CTkFrame(win)
        ovl_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(ovl_frame, text="🎞️ Pre-process Overlay", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=15, pady=(10, 3))
        
        ctk.CTkLabel(ovl_frame, text=(
            "Chuyển đổi overlay về 1920×1080 và nén H264 CRF 18.\n"
            "File gốc sẽ được thay thế bằng file nhỏ hơn (giữ chất lượng).\n"
            "Khi render: bỏ qua bước resize → nhanh hơn ~4-6ms/frame."
        ), text_color="gray", font=ctk.CTkFont(size=12), justify="left").pack(anchor="w", padx=15, pady=(0, 8))
        
        # Status
        done, total = self._count_overlay_status()
        if total == 0:
            status_text = "Chưa có overlay nào"
            status_color = "gray"
        elif done == total:
            status_text = f"✅ {done}/{total} overlay đã tối ưu"
            status_color = "#27ae60"
        elif done > 0:
            status_text = f"⚡ {done}/{total} overlay đã tối ưu"
            status_color = "#f39c12"
        else:
            status_text = f"⏳ {total} overlay chưa tối ưu"
            status_color = "#e74c3c"
        
        lbl_status = ctk.CTkLabel(ovl_frame, text=status_text, font=ctk.CTkFont(size=14, weight="bold"), text_color=status_color)
        lbl_status.pack(pady=(0, 5))
        
        # Progress bar (ẩn mặc định)
        progress = ctk.CTkProgressBar(ovl_frame)
        progress.pack(fill="x", padx=15, pady=(0, 5))
        progress.set(0)
        progress.pack_forget()
        
        lbl_progress = ctk.CTkLabel(ovl_frame, text="", text_color="gray", font=ctk.CTkFont(size=11))
        lbl_progress.pack(pady=(0, 10))
        lbl_progress.pack_forget()
        
        def do_preprocess():
            if not self.overlay_folder or not os.path.exists(self.overlay_folder):
                messagebox.showwarning("Chưa chọn", "Vui lòng chọn thư mục Overlay trước!", parent=win)
                return
            
            valid = ('.mp4', '.mov', '.avi', '.mkv')
            overlays = [os.path.join(self.overlay_folder, f) for f in os.listdir(self.overlay_folder)
                        if f.lower().endswith(valid)]
            todo = [o for o in overlays if not self._is_overlay_optimized(o)]
            
            if not todo:
                messagebox.showinfo("Đã xong", "Tất cả overlay đã được tối ưu!", parent=win)
                return
            
            # Tính dung lượng trước
            total_before = sum(os.path.getsize(o) for o in todo) / (1024*1024)
            
            btn_run.configure(state="disabled", text="⏳ Đang xử lý...")
            progress.pack(fill="x", padx=15, pady=(0, 5))
            lbl_progress.pack(pady=(0, 10))
            
            def _run():
                total_saved = 0
                for i, ovl in enumerate(todo):
                    name = os.path.basename(ovl)
                    self.after(0, lambda n=name, idx=i: (
                        lbl_progress.configure(text=f"[{idx+1}/{len(todo)}] {n}"),
                        progress.set((idx + 0.5) / len(todo))
                    ))
                    
                    # Encode ra file tạm cùng thư mục
                    temp_out = ovl + ".tmp_opt.mp4"
                    try:
                        size_before = os.path.getsize(ovl)
                        cmd = [get_ffmpeg(), '-y', '-i', ovl, '-vf',
                               'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1',
                               '-c:v', 'libx264', '-crf', '18', '-preset', 'slow',
                               '-c:a', 'copy', temp_out]
                        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                        p.wait()
                        
                        if p.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
                            size_after = os.path.getsize(temp_out)
                            saved = (size_before - size_after) / (1024*1024)
                            total_saved += saved
                            # Thay thế file gốc
                            os.remove(ovl)
                            # Đổi tên: giữ tên gốc nhưng đuôi .mp4
                            new_name = os.path.splitext(ovl)[0] + '.mp4'
                            os.rename(temp_out, new_name)
                        else:
                            if os.path.exists(temp_out): os.remove(temp_out)
                    except Exception:
                        if os.path.exists(temp_out):
                            try: os.remove(temp_out)
                            except: pass
                
                def _done():
                    d, t = self._count_overlay_status()
                    lbl_status.configure(text=f"✅ {d}/{t} overlay đã tối ưu", text_color="#27ae60")
                    progress.set(1.0)
                    lbl_progress.configure(text=f"Xong! Giảm {total_saved:.0f}MB")
                    btn_run.configure(state="normal", text="🔄 Chạy lại")
                    self.btn_speed.configure(fg_color="#27ae60")
                self.after(0, _done)
            
            threading.Thread(target=_run, daemon=True).start()
        
        btn_run = ctk.CTkButton(ovl_frame, text="⚡ Bắt đầu tối ưu", height=40,
                                 fg_color="#e67e22", hover_color="#f39c12",
                                 font=ctk.CTkFont(size=14, weight="bold"), command=do_preprocess)
        btn_run.pack(fill="x", padx=15, pady=(0, 15))
        
        # Tip
        ctk.CTkLabel(win, text="💡 Chỉ cần làm 1 lần. Mọi video sau đều được hưởng lợi.",
                     text_color="#7f8c8d", font=ctk.CTkFont(size=11)).pack(pady=(5, 5))
        
        ctk.CTkButton(win, text="Đóng", width=100, fg_color="#555555", command=win.destroy).pack(pady=(5, 15))

    # ==========================================
    # 🎧 AUDIO MIXER
    # ==========================================
    def _get_config_path(self):
        return os.path.join(APP_DIR, "volume_defaults.json")

    def _load_default_volumes(self):
        try:
            with open(self._get_config_path(), 'r') as f:
                cfg = json.load(f)
            v = cfg.get('voice_vol', 100); b = cfg.get('bgm_vol', 20)
            self.slider_voice_vol.set(v); self.lbl_voice_vol.configure(text=f"{v}%")
            self.slider_bgm_vol.set(b); self.lbl_bgm_vol.configure(text=f"{b}%")
        except Exception:
            pass

    def _save_default_volumes(self, voice_vol, bgm_vol):
        try:
            with open(self._get_config_path(), 'w') as f:
                json.dump({'voice_vol': voice_vol, 'bgm_vol': bgm_vol}, f)
        except Exception:
            pass

    def open_audio_mixer(self):
        if not self.voice_file:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn Voice trước!")
            return
        if not self.bgm_folder or not os.path.exists(self.bgm_folder):
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn thư mục BGM trước!")
            return
        valid_exts = ('.mp3', '.wav', '.m4a')
        bgms = [os.path.join(self.bgm_folder, f) for f in os.listdir(self.bgm_folder) if f.lower().endswith(valid_exts)]
        if not bgms:
            messagebox.showwarning("Trống", "Thư mục BGM trống!")
            return

        voice_dur = self.get_audio_duration(self.voice_file)
        if voice_dur <= 0:
            messagebox.showwarning("Lỗi", "Không đọc được thời lượng voice!")
            return
        chosen_bgm = random.choice(bgms)

        # --- State ---
        mx = {
            'voice': self.voice_file, 'bgm': chosen_bgm, 'dur': voice_dur,
            'play_proc': None, 'gen_proc': None, 'playing': False, 'play_start_time': 0, 'play_seek': 0
        }
        mx['temp_mix'] = os.path.join(TEMP_DIR, '_mixer_preview.wav')

        # --- Window ---
        win = ctk.CTkToplevel(self)
        win.title("🎧 Audio Mixer — Nghe thử & Cân bằng âm lượng")
        win.geometry("760x560")
        win.resizable(True, False)
        win.grab_set(); win.focus_force()
        win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 760) // 2
        y = self.winfo_y() + (self.winfo_height() - 560) // 2
        win.geometry(f"+{x}+{y}")

        ctk.CTkLabel(win, text="🎧 AUDIO MIXER", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(10, 2))
        ctk.CTkLabel(win, text=f"Voice: {os.path.basename(mx['voice'])}  |  BGM: {os.path.basename(mx['bgm'])}",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(pady=(0, 8))

        # --- Voice volume + waveform ---
        vf1 = ctk.CTkFrame(win, fg_color="transparent")
        vf1.pack(fill="x", padx=20)
        vf1.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(vf1, text="🎤 Voice:", font=ctk.CTkFont(weight="bold"), width=80).grid(row=0, column=0)
        sl_v = ctk.CTkSlider(vf1, from_=0, to=200, number_of_steps=200)
        sl_v.set(int(self.slider_voice_vol.get()))
        sl_v.grid(row=0, column=1, sticky="ew", padx=5)
        lb_v = ctk.CTkLabel(vf1, text=f"{int(sl_v.get())}%", width=45)
        lb_v.grid(row=0, column=2)
        sl_v.configure(command=lambda v: lb_v.configure(text=f"{int(v)}%"))

        lbl_vwave = ctk.CTkLabel(win, text="⏳ Đang tạo waveform...", height=65, fg_color="#0d1b2a", corner_radius=6)
        lbl_vwave.pack(fill="x", padx=20, pady=(3, 0))

        # --- BGM volume + waveform ---
        vf2 = ctk.CTkFrame(win, fg_color="transparent")
        vf2.pack(fill="x", padx=20, pady=(10, 0))
        vf2.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(vf2, text="🎵 BGM:", font=ctk.CTkFont(weight="bold"), width=80).grid(row=0, column=0)
        sl_b = ctk.CTkSlider(vf2, from_=0, to=100, number_of_steps=100)
        sl_b.set(int(self.slider_bgm_vol.get()))
        sl_b.grid(row=0, column=1, sticky="ew", padx=5)
        lb_b = ctk.CTkLabel(vf2, text=f"{int(sl_b.get())}%", width=45)
        lb_b.grid(row=0, column=2)
        sl_b.configure(command=lambda v: lb_b.configure(text=f"{int(v)}%"))

        lbl_bwave = ctk.CTkLabel(win, text="⏳ Đang tạo waveform...", height=65, fg_color="#0d2b1b", corner_radius=6)
        lbl_bwave.pack(fill="x", padx=20, pady=(3, 0))

        # --- Timeline ---
        tf = ctk.CTkFrame(win, fg_color="transparent")
        tf.pack(fill="x", padx=20, pady=(15, 0))
        tf.grid_columnconfigure(1, weight=1)

        lbl_cur = ctk.CTkLabel(tf, text="0:00", width=50, font=ctk.CTkFont(family="Consolas", size=13))
        lbl_cur.grid(row=0, column=0)
        seek_sl = ctk.CTkSlider(tf, from_=0, to=max(voice_dur, 1), number_of_steps=max(int(voice_dur * 2), 1))
        seek_sl.set(0)
        seek_sl.grid(row=0, column=1, sticky="ew", padx=8)
        lbl_tot = ctk.CTkLabel(tf, text=self.format_eta(voice_dur), width=50, font=ctk.CTkFont(family="Consolas", size=13))
        lbl_tot.grid(row=0, column=2)

        def on_seek(v):
            m, s = int(float(v)) // 60, int(float(v)) % 60
            lbl_cur.configure(text=f"{m}:{s:02d}")
        seek_sl.configure(command=on_seek)

        # --- Play controls ---
        cf = ctk.CTkFrame(win, fg_color="transparent")
        cf.pack(pady=10)

        lbl_play_status = ctk.CTkLabel(cf, text="", text_color="#f39c12", width=200)
        lbl_play_status.pack(pady=(0, 5))

        btn_frame = ctk.CTkFrame(cf, fg_color="transparent")
        btn_frame.pack()

        def stop_audio():
            mx['playing'] = False
            if mx['play_proc']:
                try: mx['play_proc'].kill()
                except: pass
                mx['play_proc'] = None
            if mx['gen_proc']:
                try: mx['gen_proc'].kill()
                except: pass
                mx['gen_proc'] = None
            lbl_play_status.configure(text="")
            btn_play.configure(state="normal", text="▶ Phát 15 giây")

        def play_audio():
            stop_audio()
            seek_pos = float(seek_sl.get())
            v_vol = int(sl_v.get()) / 100.0
            b_vol = int(sl_b.get()) / 100.0
            btn_play.configure(state="disabled", text="⏳ Đang tạo mix...")
            lbl_play_status.configure(text="Đang tạo bản mix...")

            def _gen_and_play():
                try:
                    cmd = [
                        get_ffmpeg(), '-y',
                        '-ss', str(seek_pos), '-i', mx['voice'],
                        '-ss', str(seek_pos), '-stream_loop', '-1', '-i', mx['bgm'],
                        '-filter_complex',
                        f'[0:a]volume={v_vol:.2f}[v];[1:a]volume={b_vol:.2f}[b];'
                        f'[v][b]amix=inputs=2:duration=first,volume=3.0[out]',
                        '-map', '[out]', '-t', '15', '-ar', '44100', '-ac', '2',
                        mx['temp_mix']
                    ]
                    mx['gen_proc'] = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                    mx['gen_proc'].wait()
                    mx['gen_proc'] = None

                    if not os.path.exists(mx['temp_mix']):
                        win.after(0, lambda: btn_play.configure(state="normal", text="▶ Phát 15 giây"))
                        return

                    mx['playing'] = True
                    mx['play_seek'] = seek_pos
                    mx['play_start_time'] = time.time()

                    mx['play_proc'] = subprocess.Popen(
                        [get_ffmpeg('ffplay'), '-nodisp', '-autoexit', '-loglevel', 'quiet', mx['temp_mix']],
                        creationflags=0x08000000
                    )

                    def _update_playhead():
                        if not mx['playing'] or not mx['play_proc']:
                            return
                        if mx['play_proc'].poll() is not None:
                            mx['playing'] = False
                            win.after(0, lambda: lbl_play_status.configure(text=""))
                            win.after(0, lambda: btn_play.configure(state="normal", text="▶ Phát 15 giây"))
                            return
                        elapsed = time.time() - mx['play_start_time']
                        cur = mx['play_seek'] + elapsed
                        if cur <= mx['dur']:
                            seek_sl.set(cur)
                            on_seek(cur)
                        win.after(200, _update_playhead)

                    win.after(0, lambda: btn_play.configure(state="normal", text="⏸ Đang phát..."))
                    win.after(0, lambda: lbl_play_status.configure(text=f"▶ Phát từ {int(seek_pos)//60}:{int(seek_pos)%60:02d}"))
                    win.after(100, _update_playhead)

                except Exception:
                    win.after(0, lambda: btn_play.configure(state="normal", text="▶ Phát 15 giây"))
                    win.after(0, lambda: lbl_play_status.configure(text="⚠️ Lỗi phát âm thanh"))

            threading.Thread(target=_gen_and_play, daemon=True).start()

        btn_play = ctk.CTkButton(btn_frame, text="▶ Phát 15 giây", width=140, height=38,
                                  fg_color="#27ae60", hover_color="#2ecc71",
                                  font=ctk.CTkFont(size=14, weight="bold"), command=play_audio)
        btn_play.pack(side="left", padx=5)

        btn_stop = ctk.CTkButton(btn_frame, text="⏹ Dừng", width=90, height=38,
                                  fg_color="#c0392b", hover_color="#e74c3c",
                                  font=ctk.CTkFont(size=14), command=stop_audio)
        btn_stop.pack(side="left", padx=5)

        # --- Bottom buttons ---
        sep = ctk.CTkFrame(win, height=1, fg_color="#444444")
        sep.pack(fill="x", padx=20, pady=(10, 0))

        bot = ctk.CTkFrame(win, fg_color="transparent")
        bot.pack(fill="x", padx=20, pady=(10, 15))

        def apply_and_close():
            stop_audio()
            vv = int(sl_v.get()); bv = int(sl_b.get())
            self.slider_voice_vol.set(vv); self.lbl_voice_vol.configure(text=f"{vv}%")
            self.slider_bgm_vol.set(bv); self.lbl_bgm_vol.configure(text=f"{bv}%")
            win.destroy()

        def set_default_and_apply():
            vv = int(sl_v.get()); bv = int(sl_b.get())
            self._save_default_volumes(vv, bv)
            self.slider_voice_vol.set(vv); self.lbl_voice_vol.configure(text=f"{vv}%")
            self.slider_bgm_vol.set(bv); self.lbl_bgm_vol.configure(text=f"{bv}%")
            messagebox.showinfo("Đã lưu", f"Mặc định: Voice {vv}%, BGM {bv}%\nSẽ tự động áp dụng cho các lần sau.", parent=win)

        ctk.CTkButton(bot, text="📌 Set mặc định", width=130, fg_color="#e67e22",
                      command=set_default_and_apply).pack(side="left", padx=(0, 5))
        ctk.CTkButton(bot, text="✅ Áp dụng", width=100, fg_color="#27ae60",
                      command=apply_and_close).pack(side="left", padx=5)
        ctk.CTkButton(bot, text="Đóng", width=80, fg_color="#555555",
                      command=lambda: (stop_audio(), win.destroy())).pack(side="right")

        # --- Generate waveforms background ---
        def gen_waves():
            vw = os.path.join(TEMP_DIR, '_vwave.png'); bw = os.path.join(TEMP_DIR, '_bwave.png')
            wave_w = 680

            subprocess.run([get_ffmpeg(), '-y', '-i', mx['voice'],
                           '-filter_complex', f'showwavespic=s={wave_w}x60:colors=#3498db|#2980b9',
                           '-frames:v', '1', vw],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)

            subprocess.run([get_ffmpeg(), '-y', '-t', str(mx['dur']), '-stream_loop', '-1', '-i', mx['bgm'],
                           '-filter_complex', f'showwavespic=s={wave_w}x60:colors=#27ae60|#2ecc71',
                           '-frames:v', '1', bw],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)

            def _show():
                try:
                    if os.path.exists(vw):
                        iv = Image.open(vw)
                        cv = ctk.CTkImage(light_image=iv, dark_image=iv, size=(wave_w, 60))
                        lbl_vwave.configure(image=cv, text="")
                    if os.path.exists(bw):
                        ib = Image.open(bw)
                        cb = ctk.CTkImage(light_image=ib, dark_image=ib, size=(wave_w, 60))
                        lbl_bwave.configure(image=cb, text="")
                except Exception:
                    pass
            win.after(0, _show)

        threading.Thread(target=gen_waves, daemon=True).start()

        # Cleanup
        def on_close():
            stop_audio()
            try: os.remove(mx['temp_mix'])
            except: pass
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", on_close)

    def select_img_folder(self):
        folder = filedialog.askdirectory(initialdir=self.last_dir_main)
        if folder:
            self.img_folder = folder; self.last_dir_main = folder
            self.lbl_img.configure(text=os.path.basename(folder), text_color="white")
            # Mode Tạo từ Ảnh: nạp ngay 1 ảnh random từ thư mục vừa chọn lên preview
            if self.render_mode != 'jesus_split':
                self._pv_refresh_source()
            self.trigger_realtime_preview()

    def select_voice(self):
        file = filedialog.askopenfilename(initialdir=self.last_dir_main, filetypes=[("Audio", "*.mp3 *.wav *.m4a")])
        if file:
            self.voice_file = file; self.last_dir_main = os.path.dirname(file)
            self.lbl_voice.configure(text=os.path.basename(file), text_color="white")

    def _toggle_voice_picker(self, show=True):
        """Ẩn/hiện nút chọn 1 file voice."""
        try:
            if show:
                self.voice_file_frame.grid()
            else:
                self.voice_file_frame.grid_remove()
        except Exception:
            pass

    # =========================================================
    # MODE "Jesus + Video nền" — selectors + chuyển mode
    # =========================================================
    def select_bg_video_folder(self):
        folder = filedialog.askdirectory(initialdir=self.last_dir_bgvid)
        if folder:
            self.bg_video_folder = folder; self.last_dir_bgvid = folder
            self._update_img_btn_label()
            # Đổi thư mục nền → nạp video preview mới từ thư mục đó (nếu có)
            try:
                vids = self._pv_list_bg_videos()
                if vids:
                    self._pv_set_video(random.choice(vids))
            except Exception:
                pass
            if self.inline_composer is not None:
                try:
                    self.inline_composer.reload_background()
                except Exception:
                    pass

    # ---------- CHỈNH NỀN VIDEO (zoom + dịch X/Y) ----------
    def _on_bg_zoom(self, v):
        self.bg_zoom = int(round(float(v)))
        self.lbl_bg_zoom.configure(text=f"{self.bg_zoom}%")

    def _on_bg_off_x(self, v):
        self.bg_off_x = int(round(float(v)))
        self.lbl_bg_off_x.configure(text=f"{self.bg_off_x}")

    def _on_bg_off_y(self, v):
        self.bg_off_y = int(round(float(v)))
        self.lbl_bg_off_y.configure(text=f"{self.bg_off_y}")

    def _on_bg_bright(self, v):
        self.bg_brightness = int(round(float(v)))
        self.lbl_bg_bright.configure(text=str(self.bg_brightness))

    def _on_bg_contrast(self, v):
        self.bg_contrast = int(round(float(v)))
        self.lbl_bg_contrast.configure(text=f"{self.bg_contrast}%")

    def _on_bg_sat(self, v):
        self.bg_saturation = int(round(float(v)))
        self.lbl_bg_sat.configure(text=f"{self.bg_saturation}%")

    def _on_bg_effect(self, choice):
        self.bg_effect = self.BG_EFFECT_MAP.get(choice, 'none')

    def _on_xfade_toggle(self):
        _on = bool(self.sw_xfade.get())
        self.jesus_xfade_dur = 0.5 if _on else 0.0
        self.log("💡 Chuyển cảnh xfade: " + ("BẬT — mờ chồng 0.5s giữa các clip nền (mượt)"
                                             if _on
                                             else "TẮT — cắt cứng (render nền nhanh nhất)"))

    def _reset_bg_adjust(self):
        """Về mặc định: 112% (punch-in nhẹ), giữa khung, màu grade gốc."""
        self.bg_zoom, self.bg_off_x, self.bg_off_y = 112, 0, 0
        self.bg_brightness, self.bg_contrast, self.bg_saturation = 0, 103, 108
        self.bg_effect = 'none'
        self.slider_bg_zoom.set(112)
        self.slider_bg_off_x.set(0)
        self.slider_bg_off_y.set(0)
        self.slider_bg_bright.set(0)
        self.slider_bg_contrast.set(103)
        self.slider_bg_sat.set(108)
        self.bg_effect_combo.set("Không")
        self.lbl_bg_zoom.configure(text="112%")
        self.lbl_bg_off_x.configure(text="0")
        self.lbl_bg_off_y.configure(text="0")
        self.lbl_bg_bright.configure(text="0")
        self.lbl_bg_contrast.configure(text="103%")
        self.lbl_bg_sat.configure(text="108%")

    @staticmethod
    def _bg_crop_expr(W, H, zoom, off_x, off_y):
        """Trả về (SW, SH, crop_x_expr, crop_y_expr) cho filtergraph FFmpeg.
        Ảnh/video được scale phủ SW×SH rồi crop W×H tại vị trí lệch theo off_x/off_y.
        Dùng biểu thức iw/ih để tự căn đúng cả khi tỉ lệ nguồn khác 16:9."""
        z = max(1.0, min(3.0, zoom / 100.0))
        SW, SH = int(W * z), int(H * z)
        fx = 1.0 + max(-100, min(100, off_x)) / 100.0   # 0..2 (0=trái, 1=giữa, 2=phải)
        fy = 1.0 + max(-100, min(100, off_y)) / 100.0
        cx = f"(iw-{W})/2*{fx:.4f}"
        cy = f"(ih-{H})/2*{fy:.4f}"
        return SW, SH, cx, cy

    def _update_img_btn_label(self):
        """Cập nhật nút/label nguồn dữ liệu theo mode hiện tại.
        Mode video nền: nút = chọn thư mục Videos; mode ảnh: nút = chọn thư mục Ảnh."""
        if self.render_mode == "jesus_split":
            self.btn_img.configure(text="🎬 Thư mục Videos nền",
                                   command=self.select_bg_video_folder)
            if self.bg_video_folder and os.path.isdir(self.bg_video_folder):
                _is_default = os.path.normpath(self.bg_video_folder) == os.path.normpath(
                    os.path.join(APP_DIR, "bg_videos"))
                self.lbl_img.configure(
                    text=os.path.basename(self.bg_video_folder) + (" (mặc định)" if _is_default else ""),
                    text_color="white")
            else:
                self.lbl_img.configure(text="Chưa chọn...", text_color="gray")
        else:
            self.btn_img.configure(text="📂 Chọn thư mục Ảnh", command=self.select_img_folder)
            if self.img_folder:
                self.lbl_img.configure(text=os.path.basename(self.img_folder), text_color="white")
            else:
                self.lbl_img.configure(text="Chưa chọn...", text_color="gray")

    def select_jesus_folder(self):
        folder = filedialog.askdirectory(initialdir=self.last_dir_jesus)
        if folder:
            self.jesus_folder = folder; self.last_dir_jesus = folder
            self.lbl_jesus.configure(text=os.path.basename(folder), text_color="white")

    def select_jesus_specific(self):
        """Chọn 1 ảnh Jesus cụ thể cho video kế tiếp (thay vì random)."""
        init = self.jesus_folder if os.path.isdir(self.jesus_folder) else self.last_dir_jesus
        file = filedialog.askopenfilename(
            initialdir=init,
            filetypes=[("Ảnh", "*.png *.webp *.jpg *.jpeg")])
        if file:
            self.jesus_specific_file = file
            self.lbl_jesus_pick.configure(text="🎯 " + os.path.basename(file), text_color="#d2b4de")

    def clear_jesus_specific(self):
        """Xoá ảnh cụ thể → quay lại random từ thư mục."""
        self.jesus_specific_file = ""
        self.lbl_jesus_pick.configure(text="🎲 Random từ thư mục", text_color="gray")

    def _on_render_mode_change(self, choice):
        """Chuyển giữa 2 chế độ tạo video. Nút nguồn dữ liệu đổi theo mode."""
        self.bg_adjust_frame.pack_forget()
        if "Video" in choice:
            self.render_mode = "jesus_split"
            try:
                self.bg_adjust_frame.pack(pady=(4, 6), padx=5, fill="x",
                                          before=self._sub_section_lbl)
            except Exception:
                self.bg_adjust_frame.pack(pady=(4, 6), padx=5, fill="x")
        else:
            self.render_mode = "ken_burns"
        self._update_img_btn_label()
        self._toggle_voice_picker(show=True)
        # Nguồn preview đổi theo mode: video nền ↔ ảnh (chưa chọn thư mục ảnh → trống)
        self._pv_refresh_source()
        # Đổi nền composer theo mode (ảnh Ken Burns ↔ frame video nền)
        if self.inline_composer is not None:
            try:
                self.inline_composer.reload_background()
            except Exception:
                pass

    def _open_composer_from_button(self):
        """Từ nút trong khung input: mở popup composer lớn (bản chỉnh chi tiết)."""
        try:
            self.open_composer_popup()
        except Exception as e:
            self.log(f"⚠️ Không mở được trình kéo-thả: {e}")

    def open_layout_editor(self):
        """Editor kéo-thả: chỉnh VỊ TRÍ + KÍCH THƯỚC ảnh Jesus và CỠ CHỮ phụ đề,
        xem trước trực tiếp trên khung 16:9 mô phỏng video sẽ xuất ra."""
        import tkinter as tk
        from PIL import Image, ImageTk

        CW, CH = 800, 450          # canvas 16:9 (thu nhỏ của 1920x1080)
        FW, FH = 1920, 1080
        sx, sy = CW / FW, CH / FH

        # Bản nháp (Hủy sẽ bỏ, Lưu mới ghi đè)
        jl = dict(self.jesus_layout)
        tl = dict(self.text_layout)
        font_size = [int(self.slider_fontsize.get())]
        SAMPLE_TEXT = "You were meant to be alone right now,"

        # --- Biến điều khiển TÁCH NỀN (bản nháp trong editor) ---
        key_method = [getattr(self, 'jesus_key_method', 'auto')]
        key_erode = [getattr(self, 'jesus_key_erode', 0)]
        key_feather = [getattr(self, 'jesus_key_feather', 1.0)]
        # Nguồn ảnh cố định cho phiên editor (specific, hoặc 1 ảnh random giữ nguyên)
        editor_src = [None]
        try:
            if self.jesus_specific_file and os.path.isfile(self.jesus_specific_file):
                editor_src[0] = self.jesus_specific_file
            elif os.path.isdir(self.jesus_folder):
                pngs = [f for f in os.listdir(self.jesus_folder)
                        if f.lower().endswith(('.png', '.webp', '.jpg', '.jpeg'))]
                if pngs:
                    editor_src[0] = os.path.join(self.jesus_folder, random.choice(pngs))
        except Exception:
            editor_src[0] = None

        def _placeholder():
            ph = Image.new('RGBA', (600, 1000), (0, 0, 0, 0))
            from PIL import ImageDraw
            dd = ImageDraw.Draw(ph)
            dd.ellipse([200, 120, 400, 360], fill=(120, 130, 150, 220))
            dd.polygon([(300, 340), (140, 600), (130, 1000), (470, 1000), (460, 600)],
                       fill=(120, 130, 150, 220))
            return ph

        def _load_keyed():
            """Tách nền ảnh nguồn với cài đặt hiện tại → trả PIL RGBA."""
            try:
                if editor_src[0]:
                    keyed, desc = self._prepare_jesus_image(
                        editor_src[0], method=key_method[0],
                        erode=key_erode[0], feather=key_feather[0])
                    return Image.open(keyed).convert('RGBA'), desc
            except Exception as e:
                self.log(f"⚠️ Preview tách nền lỗi ({e})")
            return _placeholder(), 'ảnh mẫu'

        _pil0, _desc0 = _load_keyed()
        imgst = {'pil': _pil0, 'aspect': _pil0.width / _pil0.height}

        # --- Lấy 1 frame nền mẫu ---
        bg_pil = None
        try:
            if os.path.isdir(self.bg_video_folder):
                vids = [f for f in os.listdir(self.bg_video_folder)
                        if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))]
                if vids:
                    vpath = os.path.join(self.bg_video_folder, random.choice(vids))
                    tmpbg = os.path.join(TEMP_DIR, "layout_bg_preview.png")
                    subprocess.run([get_ffmpeg(), '-y', '-ss', '1', '-i', vpath, '-frames:v', '1',
                                    '-vf', f'scale={CW}:{CH}:force_original_aspect_ratio=increase,crop={CW}:{CH}',
                                    tmpbg, '-loglevel', 'error'],
                                   creationflags=0x08000000, timeout=20)
                    if os.path.exists(tmpbg):
                        bg_pil = Image.open(tmpbg).convert('RGB')
        except Exception:
            bg_pil = None
        if bg_pil is None:
            bg_pil = Image.new('RGB', (CW, CH), (28, 12, 40))  # tím đậm giả lập

        # --- Cửa sổ ---
        win = ctk.CTkToplevel(self)
        win.title("🎚️ Chỉnh bố cục & Xem trước")
        win.geometry(f"{CW + 270}x{max(CH + 90, 620)}")
        win.transient(self)
        try: win.grab_set()
        except Exception: pass

        canvas = tk.Canvas(win, width=CW, height=CH, bg="#0b0b0f", highlightthickness=0)
        canvas.grid(row=0, column=0, padx=10, pady=10, sticky="nw")

        refs = {}  # giữ tham chiếu ImageTk tránh bị thu gom

        # bg
        refs['bg'] = ImageTk.PhotoImage(bg_pil)
        canvas.create_image(0, 0, anchor='nw', image=refs['bg'], tags=('bg',))

        # jesus + handle + text + textbox
        canvas.create_image(0, 0, anchor='nw', tags=('jesus',))
        canvas.create_rectangle(0, 0, 0, 0, outline='#f39c12', width=2,
                                fill='#f39c12', tags=('jhandle',))
        canvas.create_rectangle(0, 0, 0, 0, outline='#3498db', width=1, dash=(4, 3), tags=('tbox',))
        canvas.create_text(0, 0, anchor='n', justify=tk.CENTER, fill='white',
                           text=SAMPLE_TEXT, tags=('text',))

        def render_jesus():
            jesus_pil = imgst['pil']; j_aspect = imgst['aspect']
            cw_px = max(8, int(jl['h'] * j_aspect * CH))
            ch_px = max(8, int(jl['h'] * CH))
            try:
                im = jesus_pil.resize((cw_px, ch_px), Image.LANCZOS)
            except Exception:
                im = jesus_pil.resize((cw_px, ch_px))
            refs['jesus'] = ImageTk.PhotoImage(im)
            x = jl['x'] * CW; y = jl['y'] * CH
            canvas.itemconfig('jesus', image=refs['jesus'])
            canvas.coords('jesus', x, y)
            hs = 9
            canvas.coords('jhandle', x + cw_px - hs, y + ch_px - hs, x + cw_px + hs, y + ch_px + hs)

        def render_text():
            import tkinter.font as tkfont
            px = max(8, int(font_size[0] * (CH / FH)))
            fnt = tkfont.Font(family="Arial", size=-px, weight="bold")
            wrap_px = max(40, int(tl['w'] * CW))
            cx = (tl['x'] + tl['w'] / 2) * CW
            ty = tl['y'] * CH
            canvas.itemconfig('text', font=fnt, width=wrap_px)
            canvas.coords('text', cx, ty)
            # khung chữ (dashed) bao quanh
            bbox = canvas.bbox('text')
            if bbox:
                canvas.coords('tbox', bbox[0] - 6, bbox[1] - 4, bbox[2] + 6, bbox[3] + 4)
            canvas.tag_raise('text')

        # --- Kéo-thả ---
        drag = {'mode': None, 'ox': 0, 'oy': 0}

        def on_jesus_press(e):
            drag['mode'] = 'jesus'; drag['ox'] = e.x - jl['x'] * CW; drag['oy'] = e.y - jl['y'] * CH
        def on_handle_press(e):
            drag['mode'] = 'resize'
        def on_text_press(e):
            drag['mode'] = 'text'; drag['ox'] = e.x - tl['x'] * CW; drag['oy'] = e.y - tl['y'] * CH

        def on_motion(e):
            if drag['mode'] == 'jesus':
                jl['x'] = min(max((e.x - drag['ox']) / CW, -0.3), 1.0)
                jl['y'] = min(max((e.y - drag['oy']) / CH, -0.3), 1.0)
                render_jesus()
            elif drag['mode'] == 'resize':
                new_h = (e.y - jl['y'] * CH) / CH
                jl['h'] = min(max(new_h, 0.15), 1.6)
                render_jesus()
                size_slider.set(jl['h'])
            elif drag['mode'] == 'text':
                tl['x'] = min(max((e.x - drag['ox']) / CW, 0.0), 0.95)
                tl['y'] = min(max((e.y - drag['oy']) / CH, 0.0), 0.95)
                render_text()
        def on_release(e):
            drag['mode'] = None

        canvas.tag_bind('jesus', '<Button-1>', on_jesus_press)
        canvas.tag_bind('jhandle', '<Button-1>', on_handle_press)
        canvas.tag_bind('text', '<Button-1>', on_text_press)
        canvas.bind('<B1-Motion>', on_motion)
        canvas.bind('<ButtonRelease-1>', on_release)

        # --- Bảng điều khiển ---
        panel = ctk.CTkScrollableFrame(win, width=240, height=CH)
        panel.grid(row=0, column=1, padx=(0, 10), pady=10, sticky="ns")
        ctk.CTkLabel(panel, text="ĐIỀU CHỈNH", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#e67e22").pack(pady=(8, 4))
        ctk.CTkLabel(panel, text="Kéo trực tiếp ảnh / chữ trên\nkhung trái. Hoặc dùng thanh trượt:",
                     font=ctk.CTkFont(size=10), text_color="gray", justify="left").pack(padx=8)

        ctk.CTkLabel(panel, text="📏 Cỡ ảnh Jesus").pack(pady=(10, 0), anchor="w", padx=10)
        def on_size(v):
            jl['h'] = float(v); render_jesus()
        size_slider = ctk.CTkSlider(panel, from_=0.3, to=1.6, command=on_size)
        size_slider.set(jl['h']); size_slider.pack(padx=10, fill="x")

        ctk.CTkLabel(panel, text="🔤 Cỡ chữ phụ đề").pack(pady=(10, 0), anchor="w", padx=10)
        font_lbl = ctk.CTkLabel(panel, text=f"{font_size[0]} px", font=ctk.CTkFont(size=11), text_color="gray")
        font_lbl.pack(anchor="w", padx=10)
        def on_font(v):
            font_size[0] = int(v); font_lbl.configure(text=f"{int(v)} px"); render_text()
        font_slider = ctk.CTkSlider(panel, from_=40, to=300, command=on_font)
        font_slider.set(font_size[0]); font_slider.pack(padx=10, fill="x")

        ctk.CTkLabel(panel, text="↔ Rộng khung chữ").pack(pady=(10, 0), anchor="w", padx=10)
        def on_width(v):
            tl['w'] = float(v); render_text()
        width_slider = ctk.CTkSlider(panel, from_=0.2, to=0.5, command=on_width)
        width_slider.set(tl['w']); width_slider.pack(padx=10, fill="x")

        # --- TÁCH NỀN (chọn phương pháp + tinh chỉnh, xem trước ngay) ---
        ctk.CTkLabel(panel, text="✂️ TÁCH NỀN ẢNH JESUS",
                     font=ctk.CTkFont(size=12, weight="bold"), text_color="#e67e22"
                     ).pack(pady=(14, 2), anchor="w", padx=10)
        key_status = ctk.CTkLabel(panel, text=f"({_desc0})", font=ctk.CTkFont(size=10),
                                  text_color="gray", wraplength=210, justify="left")

        _METHOD_MAP = {
            "Tự động (AI→vùng→màu)": "auto",
            "AI thông minh (rembg)": "rembg",
            "Theo vùng (GrabCut)": "grabcut",
            "Theo màu (phông xanh/đặc)": "color",
            "Tắt (giữ nguyên ảnh)": "none",
        }
        _METHOD_INV = {v: k for k, v in _METHOD_MAP.items()}

        def rekey():
            key_status.configure(text="⏳ đang tách nền...", text_color="#f39c12")
            panel.update_idletasks()
            pil, desc = _load_keyed()
            imgst['pil'] = pil; imgst['aspect'] = pil.width / max(pil.height, 1)
            key_status.configure(text=f"({desc})", text_color="gray")
            render_jesus()

        def on_method(choice):
            key_method[0] = _METHOD_MAP.get(choice, "auto"); rekey()
        ctk.CTkLabel(panel, text="Phương pháp:", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=10)
        method_menu = ctk.CTkOptionMenu(panel, values=list(_METHOD_MAP.keys()),
                                        command=on_method, fg_color="#8e44ad",
                                        button_color="#7d3c98")
        method_menu.set(_METHOD_INV.get(key_method[0], "Tự động (AI→vùng→màu)"))
        method_menu.pack(padx=10, fill="x", pady=(0, 2))

        erode_lbl = ctk.CTkLabel(panel, text=f"Co rìa (xoá lem nền): {key_erode[0]}px",
                                 font=ctk.CTkFont(size=11))
        erode_lbl.pack(anchor="w", padx=10, pady=(6, 0))
        def on_erode(v):
            key_erode[0] = int(v); erode_lbl.configure(text=f"Co rìa (xoá lem nền): {int(v)}px")
        erode_slider = ctk.CTkSlider(panel, from_=0, to=8, number_of_steps=8, command=on_erode)
        erode_slider.set(key_erode[0]); erode_slider.pack(padx=10, fill="x")

        feather_lbl = ctk.CTkLabel(panel, text=f"Mượt rìa: {key_feather[0]:.1f}",
                                   font=ctk.CTkFont(size=11))
        feather_lbl.pack(anchor="w", padx=10, pady=(6, 0))
        def on_feather(v):
            key_feather[0] = round(float(v), 1); feather_lbl.configure(text=f"Mượt rìa: {float(v):.1f}")
        feather_slider = ctk.CTkSlider(panel, from_=0, to=5, command=on_feather)
        feather_slider.set(key_feather[0]); feather_slider.pack(padx=10, fill="x")

        ctk.CTkButton(panel, text="🔄 Áp dụng & xem lại", command=rekey,
                      fg_color="#d35400", hover_color="#e67e22").pack(pady=(8, 2), padx=10, fill="x")
        key_status.pack(anchor="w", padx=10)

        def do_reset():
            jl.update({'x': 0.02, 'y': 0.05, 'h': 0.95})
            tl.update({'x': 0.50, 'y': 0.34, 'w': 0.46})
            font_size[0] = 90
            size_slider.set(jl['h']); font_slider.set(font_size[0]); width_slider.set(tl['w'])
            font_lbl.configure(text=f"{font_size[0]} px")
            render_jesus(); render_text()

        def do_save():
            self.jesus_layout = dict(jl)
            self.text_layout = dict(tl)
            self.jesus_key_method = key_method[0]
            self.jesus_key_erode = key_erode[0]
            self.jesus_key_feather = key_feather[0]
            try:
                self.slider_fontsize.set(font_size[0])
            except Exception:
                pass
            self.log(f"🎚️ Đã lưu: bố cục Jesus h={jl['h']:.2f} | chữ {font_size[0]}px | "
                     f"tách nền={key_method[0]} co={key_erode[0]} mượt={key_feather[0]:.1f}")
            win.destroy()

        ctk.CTkButton(panel, text="↺ Mặc định", command=do_reset,
                      fg_color="gray30", hover_color="gray40").pack(pady=(16, 4), padx=10, fill="x")
        ctk.CTkButton(panel, text="💾 Lưu bố cục", command=do_save,
                      fg_color="#27ae60", hover_color="#2ecc71").pack(pady=4, padx=10, fill="x")
        ctk.CTkButton(panel, text="✖ Hủy", command=win.destroy,
                      fg_color="#c0392b", hover_color="#e74c3c").pack(pady=4, padx=10, fill="x")

        render_jesus()
        render_text()

    def _export_debug_bundle_action(self):
        """Xuất Debug Bundle (zip toàn bộ session log) để user gửi dev."""
        if not DEBUG_LOGGER_AVAILABLE:
            messagebox.showwarning(
                "Debug logger không khả dụng",
                "Module debug_logger không khởi tạo được.\nXem app_debug.log thay thế."
            )
            return
        try:
            # Cho user chọn nơi lưu
            default_name = f"van_pham_debug_{int(time.time())}.zip"
            target = filedialog.asksaveasfilename(
                title="Lưu Debug Bundle",
                defaultextension=".zip",
                initialfile=default_name,
                filetypes=[("ZIP Archive", "*.zip")]
            )
            if not target:
                return
            
            bundle_path = debug_logger.export_debug_bundle(target_path=target)
            if bundle_path and os.path.exists(bundle_path):
                size_kb = os.path.getsize(bundle_path) / 1024
                self.log(f"📤 Đã xuất Debug Bundle: {bundle_path} ({size_kb:.0f} KB)")
                if messagebox.askyesno(
                    "Xuất xong",
                    f"Đã lưu Debug Bundle ({size_kb:.0f} KB) tại:\n{bundle_path}\n\n"
                    f"Mở folder chứa file?"
                ):
                    try:
                        os.startfile(os.path.dirname(bundle_path))
                    except Exception:
                        pass
            else:
                messagebox.showerror("Lỗi", "Không tạo được Debug Bundle.")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Lỗi xuất bundle:\n{e}")

    def select_txt(self):
        file = filedialog.askopenfilename(initialdir=self.last_dir_main, filetypes=[("Text Script", "*.txt")])
        if file:
            self.txt_file = file; self.last_dir_main = os.path.dirname(file)
            self.lbl_txt.configure(text=os.path.basename(file), text_color="white")
            # Phụ đề preview lấy chữ từ kịch bản mới chọn → dựng lại ngay
            self.trigger_realtime_preview()

    def select_bgm(self):
        folder = filedialog.askdirectory(initialdir=self.last_dir_bgm)
        if folder: 
            self.bgm_folder = folder; self.last_dir_bgm = folder 
            self.lbl_bgm.configure(text=os.path.basename(folder), text_color="white")

    def select_overlay(self):
        folder = filedialog.askdirectory(initialdir=self.last_dir_overlay)
        if folder:
            self.overlay_folder = folder; self.last_dir_overlay = folder
            self.lbl_overlay.configure(text=os.path.basename(folder), text_color="white")

    def select_overlay_specific(self):
        """Chọn 1 file overlay (particle) CỤ THỂ — áp cho video xuất + hiện ngay trên preview."""
        init = self.overlay_folder if os.path.isdir(self.overlay_folder) else self.last_dir_overlay
        file = filedialog.askopenfilename(
            initialdir=init, filetypes=[("Video overlay", "*.mp4 *.mov *.avi")])
        if file:
            self.overlay_specific_file = file
            self.last_dir_overlay = os.path.dirname(file)
            self.lbl_overlay_pick.configure(text="🎯 " + os.path.basename(file),
                                            text_color="#48c9b0")
            # Live preview: áp overlay ngay lập tức
            self._pv_ov_path = file
            self._pv_ov_reopen = True
            self._pv_step_once = True
            self._pv_ensure_thread()
            self.log(f"✨ Overlay cụ thể: {os.path.basename(file)} (đã áp lên preview)")

    def clear_overlay_specific(self):
        """Bỏ overlay cụ thể → quay lại random từ thư mục; gỡ khỏi preview."""
        self.overlay_specific_file = ""
        self.lbl_overlay_pick.configure(text="🎲 Random từ thư mục overlay", text_color="gray")
        self._pv_ov_path = ""
        self._pv_ov_reopen = True
        self._pv_ov_frame = None
        self._pv_step_once = True

    def select_sfx_wipe(self):
        file = filedialog.askopenfilename(initialdir=self.last_dir_sfx, filetypes=[("Audio", "*.mp3 *.wav")])
        if file: 
            self.sfx_wipe_file = file; self.last_dir_sfx = os.path.dirname(file) 
            self.lbl_sfx_wipe.configure(text=os.path.basename(file), text_color="white")

    def select_sfx_blink(self):
        file = filedialog.askopenfilename(initialdir=self.last_dir_sfx, filetypes=[("Audio", "*.mp3 *.wav")])
        if file: 
            self.sfx_blink_file = file; self.last_dir_sfx = os.path.dirname(file) 
            self.lbl_sfx_blink.configure(text=os.path.basename(file), text_color="white")

    def select_out_folder(self):
        folder = filedialog.askdirectory(initialdir=self.last_dir_main)
        if folder: 
            self.output_folder = folder; self.lbl_out.configure(text=os.path.basename(folder), text_color="white")

    def trigger_realtime_preview(self):
        """Chỉnh phụ đề/bố cục → dựng lại LỚP PHỤ ĐỀ của live preview (debounce).
        Các thay đổi khác (zoom/dịch, layer) hiển thị tức thì vì player đọc state mỗi frame."""
        if self.preview_timer: self.after_cancel(self.preview_timer)
        self.preview_timer = self.after(400, self._pv_rebuild_sub_async)

    # ---------- TÙY CHỈNH CHỮ (kiểu CapCut) ----------
    def _on_sub_case_change(self, choice):
        self.sub_case = {'AA': 'upper', 'Aa': 'title', 'aa': 'lower'}.get(choice, 'keep')
        self.trigger_realtime_preview()

    def _pick_text_color(self):
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=self.sub_text_color, title="Chọn màu chữ")
        if c and c[1]:
            self.sub_text_color = c[1]
            self.btn_text_color.configure(fg_color=c[1])
            self.trigger_realtime_preview()

    def _pick_outline_color(self):
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=self.sub_outline_color, title="Chọn màu viền")
        if c and c[1]:
            self.sub_outline_color = c[1]
            self.btn_outline_color.configure(fg_color=c[1])
            self.trigger_realtime_preview()

    def _on_glow_change(self, v):
        self.sub_glow = int(round(float(v)))
        self.lbl_glow_val.configure(text=str(self.sub_glow))
        self.trigger_realtime_preview()

    @staticmethod
    def _safe_filename(name):
        """Làm sạch tên file: bỏ ký tự Windows cấm + XUỐNG DÒNG/tab (hay dính khi
        dán tiêu đề YouTube), gộp khoảng trắng thừa, bỏ chấm/cách cuối, giới hạn dài."""
        name = re.sub(r'[\\/*?:"<>|\r\n\t\x00-\x1f]', " ", str(name))
        name = re.sub(r'\s+', ' ', name).strip().rstrip('. ')
        return name[:150]

    @staticmethod
    def _apply_case_text(txt, mode):
        if mode == 'upper': return txt.upper()
        if mode == 'lower': return txt.lower()
        if mode == 'title': return txt.title()
        return txt

    @staticmethod
    def _hex_to_ass(hex_color):
        """'#RRGGBB' → '&H00BBGGRR' (định dạng màu ASS)."""
        try:
            h = hex_color.lstrip('#')
            return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()
        except Exception:
            return "&H00FFFFFF"

    def _apply_ass_overrides(self, filepath, text_color=None, outline_color=None,
                             glow=0, plain=False):
        """Hậu xử lý file ASS:
        - text_color/outline_color: đổi màu chữ/viền trong các dòng Style.
        - glow > 0: chèn {\\blur} vào từng Dialogue → viền tỏa sáng mềm.
        - plain=True: SecondaryColour = màu chữ → không còn tô màu/đỏ lên từng chữ
          (dùng cho lớp phụ đề tĩnh trên preview)."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
            p_col = self._hex_to_ass(text_color) if text_color else None
            o_col = self._hex_to_ass(outline_color) if outline_color else None
            out = []
            for ln in lines:
                if ln.startswith('Style:'):
                    parts = ln.split(',')
                    # Format: Name,Fontname,Fontsize,Primary(3),Secondary(4),Outline(5),Back(6),...
                    if len(parts) > 6:
                        if p_col:
                            parts[3] = p_col
                        if plain:
                            parts[4] = parts[3]   # secondary = primary → chữ 1 màu duy nhất
                        if o_col:
                            parts[5] = o_col
                        ln = ','.join(parts)
                elif glow > 0 and ln.startswith('Dialogue:'):
                    seg = ln.split(',', 9)   # phần Text = sau dấu phẩy thứ 9
                    if len(seg) == 10:
                        seg[9] = ('{\\blur%d}' % int(glow)) + seg[9]
                        ln = ','.join(seg)
                out.append(ln)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(out) + '\n')
        except Exception:
            pass

    def update_outline_lbl(self, value):
        self.lbl_outline_val.configure(text=f"{value:.1f}"); self.trigger_realtime_preview() 
    def update_fontsize_lbl(self, value): 
        self.lbl_fontsize_val.configure(text=f"{int(value)}"); self.trigger_realtime_preview() 
    def on_font_change(self, value): self.trigger_realtime_preview() 
    def on_ratio_change(self, value): self.trigger_realtime_preview()

    # ============================================================
    # LIVE PREVIEW PLAYER — xem chỉnh sửa THỜI GIAN THỰC
    # Nền video (zoom/dịch + grade) + thành phần bố cục + phụ đề ASS thật,
    # tất cả cập nhật ngay khi kéo slider / sửa layer — như editor chuyên nghiệp.
    # ============================================================
    PV_SUB_SAMPLE = "THE LORD IS GUIDING YOUR BREAKTHROUGH TODAY"

    def _pv_list_bg_videos(self):
        exts = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
        folder = self.bg_video_folder
        if folder and os.path.isdir(folder):
            return [os.path.join(folder, f) for f in os.listdir(folder)
                    if f.lower().endswith(exts)]
        return []

    def _pv_list_images(self):
        exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
        folder = self.img_folder
        if folder and os.path.isdir(folder):
            return [os.path.join(folder, f) for f in os.listdir(folder)
                    if f.lower().endswith(exts)]
        return []

    @staticmethod
    def _pv_read_image(path):
        """Đọc ảnh thành BGR np (an toàn với đường dẫn có dấu tiếng Việt)."""
        try:
            arr = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return None
            h, w = img.shape[:2]
            if w > 1920:
                img = cv2.resize(img, (1920, int(h * 1920 / w)), interpolation=cv2.INTER_AREA)
            return img
        except Exception:
            return None

    def _pv_refresh_source(self):
        """Nạp nguồn preview theo MODE hiện tại:
        - Tạo từ Video nền → 1 video random từ thư mục videos.
        - Tạo từ Ảnh → 1 ảnh random từ thư mục ảnh; CHƯA chọn thư mục → màn hình trống."""
        try:
            self._pv_last_frame = None
            if self.render_mode == 'jesus_split':
                self._pv_image_frame = None
                vids = self._pv_list_bg_videos()
                if vids:
                    if self._pv_video_path not in vids:
                        self._pv_set_video(random.choice(vids))
                    else:
                        self._pv_reopen = True
                        self._pv_step_once = True
                        self._pv_ensure_thread()
                else:
                    self._pv_video_path = ""
                    self._pv_reopen = True
                    self.lbl_pv_src.configure(text="(chưa có video preview)", text_color="gray")
            else:
                imgs = self._pv_list_images()
                if imgs:
                    self._pv_set_image(random.choice(imgs))
                else:
                    self._pv_image_frame = None
                    self.lbl_pv_src.configure(text="(chưa chọn thư mục ảnh)", text_color="gray")
                    self._pv_step_once = True
                    self._pv_ensure_thread()
        except Exception:
            pass

    def _pv_set_image(self, path):
        """Dùng 1 ảnh làm nền preview (mode Tạo từ Ảnh)."""
        frame = self._pv_read_image(path)
        if frame is None:
            return
        self._pv_image_frame = frame
        self._pv_last_frame = None
        self._pv_step_once = True
        try:
            self.lbl_pv_src.configure(text="🖼 " + os.path.basename(path), text_color="#48c9b0")
        except Exception:
            pass
        self._pv_ensure_thread()

    def _pv_autoload(self):
        """Tự nạp nguồn preview theo mode lúc mở app (hiện frame đầu, chưa phát)."""
        self._pv_refresh_source()

    def _pv_random_video(self):
        """🎲: mode video → đổi video random; mode ảnh → đổi ảnh random."""
        if self.render_mode == 'jesus_split':
            vids = self._pv_list_bg_videos()
            if not vids:
                messagebox.showwarning("Thiếu video",
                                       "Thư mục Videos nền không có file video để preview.")
                return
            choices = [v for v in vids if v != self._pv_video_path] or vids
            self._pv_set_video(random.choice(choices))
        else:
            imgs = self._pv_list_images()
            if not imgs:
                messagebox.showwarning("Thiếu ảnh",
                                       "Hãy chọn thư mục Ảnh trước (mode Tạo từ Ảnh).")
                return
            self._pv_set_image(random.choice(imgs))

    def _pv_pick_video(self):
        """🎯: mode video → chọn video preview; mode ảnh → chọn ảnh preview."""
        if self.render_mode == 'jesus_split':
            init = self.bg_video_folder if os.path.isdir(self.bg_video_folder) else self.last_dir_bgvid
            file = filedialog.askopenfilename(
                initialdir=init, filetypes=[("Video", "*.mp4 *.mov *.avi *.mkv *.webm")])
            if file:
                self._pv_set_video(file)
        else:
            init = self.img_folder if os.path.isdir(self.img_folder) else self.last_dir_main
            file = filedialog.askopenfilename(
                initialdir=init, filetypes=[("Ảnh", "*.png *.jpg *.jpeg *.webp *.bmp")])
            if file:
                self._pv_set_image(file)

    def _pv_set_video(self, path):
        self._pv_video_path = path
        self._pv_reopen = True
        self._pv_step_once = True   # hiện ngay frame đầu kể cả khi đang pause
        try:
            self.lbl_pv_src.configure(text="🎞 " + os.path.basename(path), text_color="#48c9b0")
        except Exception:
            pass
        self._pv_ensure_thread()

    def _pv_toggle_play(self):
        if self.render_mode == 'jesus_split' and not self._pv_video_path:
            self._pv_random_video()
            if not self._pv_video_path:
                return
        self._pv_playing = not self._pv_playing
        if self._pv_playing:
            self._pv_clock = 0.0   # bấm phát → hiệu ứng chữ chạy lại từ đầu
            # Dựng chuỗi ĐỘNG (nếu chưa có) để chữ chạy hiệu ứng khi phát
            if self._pv_sub_frames is None and self.txt_file and os.path.exists(self.txt_file):
                self._pv_rebuild_sub_async(static_only=False)
        self.btn_pv_play.configure(
            text="⏸ Dừng" if self._pv_playing else "▶ Phát",
            fg_color="#c0392b" if self._pv_playing else "#27ae60",
            hover_color="#e74c3c" if self._pv_playing else "#2ecc71")
        self._pv_ensure_thread()

    def _pv_pause(self):
        if self._pv_playing:
            self._pv_toggle_play()

    def _pv_ensure_thread(self):
        if self._pv_thread is None or not self._pv_thread.is_alive():
            self._pv_stop_flag = False
            self._pv_thread = threading.Thread(target=self._pv_loop, daemon=True)
            self._pv_thread.start()
        if self._pv_sub_overlay is None and not self._pv_sub_building:
            self._pv_rebuild_sub_async()

    def _pv_loop(self):
        """Thread player: đọc frame video → dựng khung hình theo state HIỆN TẠI.
        Khi pause vẫn refresh ~7fps để kéo slider thấy thay đổi ngay trên frame đứng."""
        cap = None
        ov_cap = None
        while not self._pv_stop_flag:
            try:
                if self._pv_reopen or cap is None:
                    self._pv_reopen = False
                    if cap is not None:
                        try: cap.release()
                        except Exception: pass
                        cap = None
                    if self._pv_video_path and os.path.isfile(self._pv_video_path):
                        cap = cv2.VideoCapture(self._pv_video_path)
                # Overlay cụ thể: mở/đóng theo lựa chọn hiện tại
                if self._pv_ov_reopen or (ov_cap is None and self._pv_ov_path):
                    self._pv_ov_reopen = False
                    if ov_cap is not None:
                        try: ov_cap.release()
                        except Exception: pass
                        ov_cap = None
                    self._pv_ov_frame = None
                    if self._pv_ov_path and os.path.isfile(self._pv_ov_path):
                        ov_cap = cv2.VideoCapture(self._pv_ov_path)
                is_img_mode = (self.render_mode != 'jesus_split')
                if cap is None and not is_img_mode:
                    time.sleep(0.2); continue

                advance = self._pv_playing or self._pv_step_once
                self._pv_step_once = False
                if self._pv_playing:
                    self._pv_clock += 1 / 24   # đồng hồ phát (khớp nhịp sleep bên dưới)
                if advance:
                    if is_img_mode:
                        # Mode Tạo từ Ảnh: nền là ảnh tĩnh (None = chưa chọn thư mục)
                        self._pv_last_frame = self._pv_image_frame
                    elif cap is not None:
                        ok, frame = cap.read()
                        if not ok:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # hết video → loop lại
                            ok, frame = cap.read()
                        if ok:
                            # Thu nhỏ ngay nguồn 4K cho nhẹ (giữ đủ nét cho zoom tới 200%)
                            h, w = frame.shape[:2]
                            if w > 1920:
                                frame = cv2.resize(frame, (1920, int(h * 1920 / w)),
                                                   interpolation=cv2.INTER_AREA)
                            self._pv_last_frame = frame
                    # Overlay chạy song song (loop độc lập với nền)
                    if ov_cap is not None:
                        ok2, ovf = ov_cap.read()
                        if not ok2:
                            ov_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            ok2, ovf = ov_cap.read()
                        if ok2:
                            oh, ow = ovf.shape[:2]
                            if ow > 1280:
                                ovf = cv2.resize(ovf, (1280, int(oh * 1280 / ow)),
                                                 interpolation=cv2.INTER_AREA)
                            self._pv_ov_frame = ovf
                if self._pv_last_frame is None:
                    # Chưa có nguồn (vd mode Ảnh chưa chọn thư mục) → màn hình trống
                    if not self._pv_busy:
                        self._pv_busy = True
                        try:
                            PW, PH = self._pv_res
                            self.after(0, self._pv_show, Image.new('RGB', (PW, PH), (8, 8, 10)))
                        except Exception:
                            self._pv_busy = False
                    time.sleep(0.25); continue

                composed = self._pv_compose(self._pv_last_frame)
                if composed is not None and not self._pv_busy:
                    self._pv_busy = True
                    try:
                        self.after(0, self._pv_show, composed)
                    except Exception:
                        self._pv_busy = False
                time.sleep(1 / 24 if self._pv_playing else 0.15)
            except Exception:
                time.sleep(0.3)
        for _c in (cap, ov_cap):
            if _c is not None:
                try: _c.release()
                except Exception: pass

    def _pv_vignette_mask(self, PW, PH):
        """Mask vignette (cache theo kích thước) — góc tối dần như filter vignette."""
        key = (PW, PH)
        cached = getattr(self, '_pv_vig_cache', {}).get(key)
        if cached is not None:
            return cached
        yy, xx = np.mgrid[0:PH, 0:PW].astype(np.float32)
        nx = (xx - PW / 2) / (PW / 2)
        ny = (yy - PH / 2) / (PH / 2)
        d = np.sqrt(nx * nx + ny * ny) / np.sqrt(2)
        mask = np.clip(1.0 - 0.55 * (d ** 2.2), 0.3, 1.0)[..., None]
        if not hasattr(self, '_pv_vig_cache'):
            self._pv_vig_cache = {}
        self._pv_vig_cache[key] = mask
        return mask

    def _pv_show(self, pil_img):
        """Đẩy frame nền + lớp phụ đề vào canvas composer (layer do composer tự vẽ,
        vẫn kéo-thả được NGAY TRÊN video đang chạy)."""
        try:
            comp = self.inline_composer
            if comp is not None:
                # Chọn frame phụ đề theo đồng hồ phát → chữ CHẠY đúng kiểu đang chọn
                sub = self._pv_sub_overlay
                frames = self._pv_sub_frames
                if frames:
                    if self._pv_playing:
                        idx = int((self._pv_clock % self.PV_SUB_DUR) * self.PV_SUB_FPS)
                        sub = frames[min(idx, len(frames) - 1)]
                    else:
                        sub = frames[-1]   # pause → trạng thái cuối (đủ chữ)
                comp.set_live_frame(pil_img, sub)
        finally:
            self._pv_busy = False

    def _pv_compose(self, frame_bgr):
        """Dựng NỀN video cho preview: zoom/dịch khung + color grade.
        (Thành phần bố cục + ô phụ đề do composer vẽ đè lên — kéo thả trực tiếp.)"""
        try:
            PW, PH = self._pv_res
            img = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            ih, iw = img.shape[:2]
            z = max(1.0, min(3.0, self.bg_zoom / 100.0)) if self.render_mode == 'jesus_split' else 1.0
            # Scale phủ (PW*z, PH*z) rồi crop PW×PH theo offset — cùng công thức với render
            sw, sh = int(PW * z), int(PH * z)
            scale = max(sw / iw, sh / ih)
            nw, nh = max(int(iw * scale), PW), max(int(ih * scale), PH)
            img = cv2.resize(img, (nw, nh),
                             interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
            fx = 1.0 + max(-100, min(100, self.bg_off_x)) / 100.0
            fy = 1.0 + max(-100, min(100, self.bg_off_y)) / 100.0
            if self.render_mode != 'jesus_split':
                fx = fy = 1.0
            x0 = max(0, min(nw - PW, int((nw - PW) / 2 * fx)))
            y0 = max(0, min(nh - PH, int((nh - PH) / 2 * fy)))
            img = img[y0:y0 + PH, x0:x0 + PW]
            if self.render_mode == 'jesus_split':
                # Xấp xỉ ánh sáng + màu + effect của render
                _b = self.bg_brightness / 100.0
                _c = self.bg_contrast / 100.0
                img = cv2.convertScaleAbs(img, alpha=_c, beta=128 * (1 - _c) + _b * 255)
                _s = self.bg_saturation / 100.0
                _fx = self.bg_effect
                if abs(_s - 1.0) > 0.02 and _fx != 'bw':
                    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32)
                    hsv[..., 1] = np.clip(hsv[..., 1] * _s, 0, 255)
                    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)
                if _fx == 'warm':
                    img = np.clip(img.astype(np.int16) + np.array([20, 5, -12]), 0, 255).astype(np.uint8)
                elif _fx == 'cool':
                    img = np.clip(img.astype(np.int16) + np.array([-12, 0, 20]), 0, 255).astype(np.uint8)
                elif _fx == 'bw':
                    img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
                elif _fx == 'sepia':
                    _k = np.array([[.393, .769, .189], [.349, .686, .168], [.272, .534, .131]],
                                  dtype=np.float32)
                    img = np.clip(img.astype(np.float32) @ _k.T, 0, 255).astype(np.uint8)
                elif _fx == 'vignette':
                    img = (img.astype(np.float32) * self._pv_vignette_mask(PW, PH)).astype(np.uint8)
            # Overlay cụ thể (particle): CỘNG SÁNG giống render (blend=addition × opacity)
            ovf = self._pv_ov_frame
            if ovf is not None:
                try:
                    ov_rgb = cv2.cvtColor(
                        cv2.resize(ovf, (PW, PH), interpolation=cv2.INTER_LINEAR),
                        cv2.COLOR_BGR2RGB)
                    k = float(getattr(self, 'jesus_overlay_opacity', 0.5))
                    if k > 0:
                        img = cv2.add(img, cv2.convertScaleAbs(ov_rgb, alpha=k, beta=0))
                except Exception:
                    pass
            return Image.fromarray(img)
        except Exception:
            return None

    def _pv_rebuild_sub_async(self, static_only=None):
        """Dựng lại lớp phụ đề ASS (chạy nền). Gọi khi đổi font/cỡ/viền/kiểu.
        static_only=True → chỉ dựng 1 frame TĨNH (nhanh, dùng khi đang chỉnh/pause).
        static_only=False → dựng cả chuỗi ĐỘNG 5s (khi phát). None → tự quyết
        theo trạng thái phát."""
        if static_only is None:
            static_only = not self._pv_playing
        if self._pv_sub_building:
            self._pv_sub_dirty = True
            self._pv_sub_dirty_static = static_only
            return
        self._pv_sub_building = True
        self._pv_sub_dirty = False
        threading.Thread(target=self._pv_build_sub_overlay,
                         kwargs={'static_only': static_only}, daemon=True).start()

    # Thông số chuỗi phụ đề ĐỘNG trên preview
    PV_SUB_FPS = 10       # fps lớp phụ đề (nhẹ, đủ mượt cho hiệu ứng chữ)
    PV_SUB_DUR = 5.0      # vòng lặp 5 giây
    PV_SUB_RW, PV_SUB_RH = 960, 540   # render 960x540 rồi scale lên preview

    def _pv_build_sub_overlay(self, static_only=True):
        """Render lớp phụ đề ASS THẬT (ĐÚNG KIỂU đang chọn) đè lên live preview.
        static_only=True → chỉ 1 frame trạng thái đủ chữ (nhanh, khi đang chỉnh).
        static_only=False → cả chuỗi 5s để chữ chạy hiệu ứng khi phát.
        Dùng đúng write_ass_subtitle của render nên khớp 100% kiểu chữ/hiệu ứng."""
        temp_ass = os.path.join(TEMP_DIR, "pv_sub.ass")
        try:
            # CHỈ hiện chữ khi ĐÃ đính kèm kịch bản — chưa có thì không hiện gì
            if not (self.txt_file and os.path.exists(self.txt_file)):
                self._pv_sub_overlay = None
                self._pv_sub_frames = None
                self._pv_step_once = True
                return
            W, H = self.get_resolution("16:9 (YouTube/Ngang)")
            # ĐÚNG kiểu phụ đề đang chọn (chữ chạy hiệu ứng ngay trên preview)
            style_mode = self.SUBTITLE_STYLE_MAP.get(self.subtitle_style_combo.get(), 'classic')
            # Chữ lấy từ file kịch bản user đính kèm (câu đầu tiên)
            preview_text = self.PV_SUB_SAMPLE
            try:
                with open(self.txt_file, 'r', encoding='utf-8', errors='replace') as tf:
                    words = tf.read().split()
                if words:
                    preview_text = ' '.join(words[:10])
            except Exception:
                pass
            preview_text = self._apply_case_text(preview_text, self.sub_case)
            tokens = preview_text.split()
            seg_dur = self.PV_SUB_DUR
            per = (seg_dur - 0.4) / max(len(tokens), 1)
            fake_words = [{'word': tk, 'start': i * per + 0.2, 'end': (i * per) + per * 0.88 + 0.2}
                          for i, tk in enumerate(tokens)]
            dummy_scene = [{'start': 0, 'end': seg_dur, 'scene_end': seg_dur,
                            'text': preview_text,
                            'is_segment_ending': False,
                            'words': fake_words}]
            outline_val = self.slider_outline.get()
            fontsize_val = int(self.slider_fontsize.get())
            font_val = self.font_combo.get()
            kb_tb = getattr(self, 'kb_text_layout', None)
            if kb_tb and kb_tb.get('enabled'):
                self.write_ass_subtitle(dummy_scene, font_val, fontsize_val, outline_val,
                                        temp_ass, W, H, 'en', style_mode, layout='right_box',
                                        text_box={'x': kb_tb['x'], 'y': kb_tb['y'], 'w': kb_tb['w'], 'h': kb_tb.get('h')})
            else:
                self.write_ass_subtitle(dummy_scene, font_val, fontsize_val, outline_val,
                                        temp_ass, W, H, 'en', style_mode)
            # Áp màu chữ/viền + glow + 1 màu duy nhất (không tô màu lên từng chữ)
            self._apply_ass_overrides(temp_ass, text_color=self.sub_text_color,
                                      outline_color=self.sub_outline_color,
                                      glow=self.sub_glow, plain=True)
            ass_escaped = self.escape_path_for_ffmpeg(temp_ass)
            # Filter 'ass' KHÔNG ghi kênh alpha → render nền ĐEN + nền TRẮNG rồi
            # tách alpha:  A = 1-(trắng-đen)/255 ;  màu = đen/A
            RW, RH, FPS_S = self.PV_SUB_RW, self.PV_SUB_RH, self.PV_SUB_FPS
            PW, PH = self._pv_res
            _resample = Image.LANCZOS if static_only else Image.BILINEAR

            if static_only:
                # === 1 FRAME TĨNH: lấy đúng lúc CUỐI (đủ chữ hiện) — nhanh ===
                _seqs = {}
                for _nm, _col in (('b', 'black'), ('w', 'white')):
                    cmd = [get_ffmpeg(), '-y',
                           '-f', 'lavfi', '-i', f'color=c={_col}:s={RW}x{RH}:r=25:d={seg_dur}',
                           '-ss', f'{max(seg_dur - 0.15, 0.1):.2f}',
                           '-vf', f"ass='{ass_escaped}'",
                           '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
                    r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000)
                    if r.returncode == 0 and len(r.stdout) >= RW * RH * 3:
                        _seqs[_nm] = np.frombuffer(
                            r.stdout[:RW * RH * 3], dtype=np.uint8
                        ).reshape(RH, RW, 3).astype(np.float32)
                if 'b' in _seqs and 'w' in _seqs:
                    _b, _w = _seqs['b'], _seqs['w']
                    _a = np.clip(1.0 - (_w - _b) / 255.0, 0, 1).mean(axis=2)
                    _rgb = np.where(_a[..., None] > 1e-3, _b / np.maximum(_a[..., None], 1e-3), 0)
                    _out = np.dstack([np.clip(_rgb, 0, 255).astype(np.uint8), (_a * 255).astype(np.uint8)])
                    _pil = Image.fromarray(_out, 'RGBA').resize((PW, PH), _resample)
                    self._pv_sub_overlay = _pil
                    self._pv_sub_frames = None      # chưa có chuỗi động (dựng khi bấm Phát)
                self._pv_step_once = True
            else:
                # === CHUỖI ĐỘNG 5s: chữ chạy hiệu ứng khi phát ===
                _seqs = {}
                for _nm, _col in (('b', 'black'), ('w', 'white')):
                    cmd = [get_ffmpeg(), '-y',
                           '-f', 'lavfi', '-i', f'color=c={_col}:s={RW}x{RH}:r={FPS_S}:d={seg_dur}',
                           '-vf', f"ass='{ass_escaped}'",
                           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
                    r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000)
                    if r.returncode == 0 and r.stdout:
                        n = len(r.stdout) // (RW * RH * 3)
                        if n > 0:
                            _seqs[_nm] = np.frombuffer(
                                r.stdout[:n * RW * RH * 3], dtype=np.uint8
                            ).reshape(n, RH, RW, 3).astype(np.float32)
                if 'b' in _seqs and 'w' in _seqs:
                    _bs, _ws = _seqs['b'], _seqs['w']
                    n = min(len(_bs), len(_ws))
                    frames = []
                    _prev_key = _prev_pil = None
                    for i in range(n):
                        _b, _w = _bs[i], _ws[i]
                        _a = np.clip(1.0 - (_w - _b) / 255.0, 0, 1).mean(axis=2)
                        _rgb = np.where(_a[..., None] > 1e-3, _b / np.maximum(_a[..., None], 1e-3), 0)
                        _out = np.dstack([np.clip(_rgb, 0, 255).astype(np.uint8), (_a * 255).astype(np.uint8)])
                        _key = _out.tobytes()      # dedupe frame trùng (style tĩnh chỉ tốn 1 ảnh)
                        if _key == _prev_key:
                            frames.append(_prev_pil); continue
                        _pil = Image.fromarray(_out, 'RGBA').resize((PW, PH), _resample)
                        frames.append(_pil)
                        _prev_key, _prev_pil = _key, _pil
                    if frames:
                        self._pv_sub_frames = frames
                        self._pv_sub_overlay = frames[-1]   # fallback khi pause
        except Exception:
            pass
        finally:
            try: os.remove(temp_ass)
            except Exception: pass
            self._pv_sub_building = False
            if self._pv_sub_dirty:
                _so = getattr(self, '_pv_sub_dirty_static', True)
                try: self.after(0, lambda: self._pv_rebuild_sub_async(static_only=_so))
                except Exception: pass

    def log(self, message):
        # Tkinter KHÔNG thread-safe: gọi từ thread render → chuyển về main thread
        # (gọi thẳng từ worker là nguyên nhân đơ app khi lỗi bắn nhiều dòng log liên tiếp)
        if threading.current_thread() is not threading.main_thread():
            try: self.after(0, self.log, message)
            except Exception: pass
            return
        self.log_box.configure(state="normal"); self.log_box.insert("end", message + "\n"); self.log_box.see("end"); self.log_box.configure(state="disabled")
    def update_progress_ui(self, percent_val, text_info):
        if threading.current_thread() is not threading.main_thread():
            try: self.after(0, self.update_progress_ui, percent_val, text_info)
            except Exception: pass
            return
        self.progress_bar.set(percent_val); self.lbl_percent.configure(text=f"{int(percent_val*100)}%"); self.lbl_live_progress.configure(text=text_info)
    def _maximize_startup(self):
        """Maximize cửa sổ lúc mở app. CTk hay re-apply geometry trong ~0.5s đầu
        → kiểm tra lại vài lần, lần nào bị reset thì zoom lại."""
        try:
            if self.state() != 'zoomed':
                try:
                    self.state('zoomed')            # Windows
                except Exception:
                    self.attributes('-zoomed', True)  # Linux fallback
        except Exception:
            pass
        self._maximize_attempts += 1
        if self._maximize_attempts < 4:               # thử lại ở 0.2s / 0.7s / 1.2s
            self.after(500, self._maximize_startup)
        else:
            # Sau khi maximize ổn định → đặt bề rộng panel trái mặc định
            self.after(200, self._set_default_sash)

    def _set_default_sash(self):
        """Đặt vị trí thanh chia trái/phải lúc khởi động (user vẫn kéo lại được)."""
        try:
            cur_x = self.main_paned.sash_coord(0)[0]
            if cur_x < 500:   # bị co hẹp khi maximize → nới về mặc định
                self.main_paned.sash_place(0, 780, 1)
        except Exception:
            pass

    # ============================================================
    # TRÌNH KÉO-THẢ BỐ CỤC — GỘP CHUNG với màn hình xem trước
    # ============================================================
    def _calc_composer_size(self):
        """Kích thước canvas 16:9 LỚN NHẤT vừa vùng preview (trừ toolbar/chip/hint)."""
        avail_w = max(self.preview_stack.winfo_width() - 14, 360)
        avail_h = max(self.preview_stack.winfo_height() - 130, 220)  # chừa toolbar + chips + hint
        cw = min(avail_w, int(avail_h * 16 / 9), 1600)
        cw = max(cw, 360)
        return cw, int(cw * 9 / 16)

    def _on_preview_area_resize(self, event=None):
        """Cửa sổ đổi kích thước → co giãn canvas preview theo (debounce)."""
        if self.inline_composer is None:
            return
        if self._pv_resize_timer:
            self.after_cancel(self._pv_resize_timer)
        self._pv_resize_timer = self.after(250, self._apply_preview_resize)

    def _apply_preview_resize(self):
        self._pv_resize_timer = None
        comp = self.inline_composer
        if comp is None:
            return
        cw, ch = self._calc_composer_size()
        if abs(cw - comp.cw) < 24:   # thay đổi quá nhỏ → bỏ qua
            return
        try:
            comp.resize(cw, ch)
        except Exception:
            return
        self._pv_res = (cw, ch)
        self._pv_sub_overlay = None
        self._pv_sub_frames = None
        self._pv_rebuild_sub_async()
        self._pv_step_once = True

    def _ensure_inline_composer(self, rebuild=False):
        """Tạo (hoặc dựng lại) composer nhúng trong panel phải."""
        if not COMPOSER_AVAILABLE:
            return
        if self.inline_composer is not None and not rebuild:
            self.inline_composer.render_all()
            return
        if self.inline_composer is not None:
            self.inline_composer.destroy()
            self.inline_composer = None
        # Kích thước canvas: LỚN TỐI ĐA theo vùng preview hiện có (giữ 16:9)
        self.update_idletasks()
        cw, ch = self._calc_composer_size()
        self.inline_composer = LayoutComposer(
            self, self.composer_host, cw, ch, show_panel=False,
            on_expand=self.open_composer_popup,
            on_render_frame=None,
            on_change=self.trigger_realtime_preview)
        # Player dựng frame đúng kích thước canvas composer
        self._pv_res = (cw, ch)
        self._pv_sub_overlay = None   # dựng lại lớp phụ đề theo kích thước mới
        self._pv_sub_frames = None
        self._pv_rebuild_sub_async()
        self._pv_step_once = True

    def open_composer_popup(self):
        """Mở cửa sổ composer lớn, kèm bảng chỉnh chi tiết từng layer."""
        if not COMPOSER_AVAILABLE:
            return
        CW, CH = 960, 540
        win = ctk.CTkToplevel(self)
        win.title("🎚️ Trình kéo-thả bố cục — Chế độ Ảnh (Ken Burns)")
        win.geometry(f"{CW + 40}x{CH + 160}")
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(win,
                     text="Kéo để di chuyển · kéo góc vàng để đổi kích thước · bấm chọn rồi chỉnh ở cột phải",
                     font=ctk.CTkFont(size=12), text_color="#bbbbbb"
                     ).pack(pady=(8, 2), padx=10, anchor="w")

        host = ctk.CTkFrame(win, fg_color="transparent")
        host.pack(fill="both", expand=True, padx=10, pady=6)

        popup_composer = LayoutComposer(
            self, host, CW, CH, show_panel=True,
            on_expand=None, on_render_frame=None,
            on_change=self.trigger_realtime_preview)

        def _close():
            try:
                popup_composer.destroy()
            except Exception:
                pass
            win.destroy()
            # Đồng bộ lại composer nhúng
            if self.inline_composer is not None:
                self.inline_composer._thumb_cache.clear()
                self.inline_composer.render_all()
            self.log(f"🎚️ Đã cập nhật bố cục: {len(self.overlay_layers)} thành phần.")

        btnbar = ctk.CTkFrame(win, fg_color="transparent")
        btnbar.pack(fill="x", padx=10, pady=(0, 8))
        ctk.CTkButton(btnbar, text="↺ Đổi ảnh nền mẫu",
                      command=popup_composer.reload_background,
                      fg_color="gray30", hover_color="gray40").pack(side="left")
        ctk.CTkButton(btnbar, text="✅ Xong", command=_close,
                      fg_color="#27ae60", hover_color="#2ecc71").pack(side="right")
        win.protocol("WM_DELETE_WINDOW", _close)

    def _grab_video_frame(self, video_path, t=1.0):
        """Lấy 1 frame từ video (cho thumbnail phông xanh trong editor). Trả PIL RGB hoặc None."""
        try:
            if not video_path or not os.path.isfile(video_path):
                return None
            out = os.path.join(TEMP_DIR, f"_vframe_{hashlib.md5(video_path.encode()).hexdigest()[:8]}.png")
            subprocess.run(
                [get_ffmpeg(), '-y', '-ss', str(t), '-i', video_path,
                 '-frames:v', '1', out, '-loglevel', 'error'],
                creationflags=0x08000000, timeout=8)   # file hỏng → bỏ qua sau 8s, không treo UI lâu
            if os.path.exists(out):
                return Image.open(out).convert('RGB')
        except Exception as e:
            self.log(f"⚠️ Không lấy được frame video ({e})")
        return None

    def escape_path_for_ffmpeg(self, path): return path.replace('\\', '/').replace(':', '\\:')
    def format_ass_time(self, seconds):
        h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"
    def format_eta(self, seconds):
        if seconds < 0: return "00:00"
        h = int(seconds // 3600); m = int((seconds % 3600) // 60); s = int(seconds % 60)
        if h > 0: return f"{h}h {m:02d}m {s:02d}s"
        return f"{m:02d}m {s:02d}s"

    def update_queue_ui(self):
        # Xóa tất cả widget cũ trong scroll frame
        for widget in self.queue_scroll.winfo_children():
            widget.destroy()
        
        if not self.render_queue:
            lbl_empty = ctk.CTkLabel(self.queue_scroll, text="Danh sách trống.\nHãy cấu hình và bấm 'THÊM VÀO HÀNG ĐỢI'.", text_color="gray", font=ctk.CTkFont(size=14))
            lbl_empty.pack(pady=30)
            return
        
        lbl_count = ctk.CTkLabel(self.queue_scroll, text=f"🎬 Đang chờ: {len(self.render_queue)} Video", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f39c12")
        lbl_count.pack(pady=(5, 10), anchor="w", padx=5)
        
        for i, task in enumerate(self.render_queue):
            self._create_queue_card(i, task)

    def _create_queue_card(self, index, task):
        voice_display = task.get('voice_file', '') and os.path.basename(task['voice_file']) or "(chưa chọn)"
        default_stem = os.path.splitext(voice_display)[0]
        voice_name_for_default = voice_display
        voice_name = voice_name_for_default  # giữ tương thích biến cũ
        txt_name = os.path.basename(task['txt_file'])
        img_folder_name = os.path.basename(task['img_folder'])
        custom_name = task.get('custom_name', '').strip()

        if custom_name:
            safe_name = self._safe_filename(custom_name)
            display_name = f"{safe_name}.mp4"
        else:
            display_name = f"FINAL_{default_stem}.mp4"
        
        card = ctk.CTkFrame(self.queue_scroll, border_width=1, border_color="#444444")
        card.pack(fill="x", padx=5, pady=3)
        card.grid_columnconfigure(0, weight=1)
        
        # Thông tin
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        
        lbl_title = ctk.CTkLabel(info_frame, text=f"[{index+1}] 📽️ {display_name}", font=ctk.CTkFont(size=13, weight="bold"), anchor="w")
        lbl_title.pack(anchor="w")
        
        lang = task.get('language', 'English')
        sub_style = task.get('subtitle_style', 'Cổ điển')
        mode_badge = ("🎬 Video nền" if task.get('render_mode') == 'jesus_split' else "🖼️ Ken Burns")
        detail_text = f"{mode_badge}  |  Voice: {voice_display}  |  Kịch bản: {txt_name}  |  🌐 {lang}  |  📝 {sub_style}"
        lbl_detail = ctk.CTkLabel(info_frame, text=detail_text, text_color="gray", font=ctk.CTkFont(size=11), anchor="w")
        lbl_detail.pack(anchor="w")
        
        # Nút hành động
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.grid(row=0, column=1, padx=(0, 8), pady=8)
        
        btn_up = ctk.CTkButton(btn_frame, text="▲", width=30, height=30, fg_color="#555555", hover_color="#777777",
                                command=lambda t=task: self.move_queue_item(t, -1))
        btn_up.pack(side="left", padx=2)

        btn_down = ctk.CTkButton(btn_frame, text="▼", width=30, height=30, fg_color="#555555", hover_color="#777777",
                                  command=lambda t=task: self.move_queue_item(t, 1))
        btn_down.pack(side="left", padx=2)

        btn_edit = ctk.CTkButton(btn_frame, text="✏️", width=36, height=30, fg_color="#2980b9", hover_color="#3498db",
                                  command=lambda t=task: self.edit_queue_item(t))
        btn_edit.pack(side="left", padx=2)

        btn_delete = ctk.CTkButton(btn_frame, text="🗑️", width=36, height=30, fg_color="#c0392b", hover_color="#e74c3c",
                                    command=lambda t=task: self.delete_queue_item(t))
        btn_delete.pack(side="left", padx=2)

    def move_queue_item(self, task, delta):
        """Di chuyển 1 video lên (-1) / xuống (+1) trong hàng chờ.
        Cho phép cả khi đang render — thao tác theo đối tượng task nên không
        lệch khi tool tự lấy video kế tiếp ra render giữa chừng."""
        if task not in self.render_queue:
            self.update_queue_ui()  # task đã bắt đầu render → chỉ vẽ lại danh sách
            return
        i = self.render_queue.index(task)
        j = i + delta
        if j < 0 or j >= len(self.render_queue):
            return  # đã ở đầu/cuối danh sách
        self.render_queue[i], self.render_queue[j] = self.render_queue[j], self.render_queue[i]
        self.update_queue_ui()

    def delete_queue_item(self, task):
        """Xóa 1 video khỏi hàng chờ — CHO PHÉP cả khi đang render video khác.
        (Video đang render đã được lấy ra khỏi hàng chờ nên không bị ảnh hưởng.
        Thao tác theo đối tượng task, không theo index → không lệch khi tool
        tự lấy video kế tiếp ra render giữa chừng.)"""
        if task not in self.render_queue:
            # Task đã bắt đầu render (hoặc đã bị xóa) trong lúc user thao tác
            messagebox.showwarning("Không thể xóa", "Video này đã bắt đầu render hoặc không còn trong hàng chờ.")
            self.update_queue_ui()
            return
        voice_name = os.path.basename(task.get('voice_file', '')) or "(chưa chọn)"
        if messagebox.askyesno("Xác nhận xóa", f"Bạn có chắc muốn xóa video:\n{voice_name}?"):
            if task in self.render_queue:  # kiểm tra lại: queue có thể đã đổi khi hộp thoại mở
                self.render_queue.remove(task)
                # Xóa preprocess cache nếu có
                task_key = self._get_task_key(task)
                self._preprocess_result.pop(task_key, None)
            self.update_queue_ui()

    def edit_queue_item(self, task):
        """Chỉnh sửa 1 video trong hàng chờ — CHO PHÉP cả khi đang render video khác."""
        if task not in self.render_queue:
            messagebox.showwarning("Không thể sửa", "Video này đã bắt đầu render hoặc không còn trong hàng chờ.")
            self.update_queue_ui()
            return
        index = self.render_queue.index(task)
        
        # --- TẠO CỬA SỔ CHỈNH SỬA ---
        edit_win = ctk.CTkToplevel(self)
        edit_win.title(f"Chỉnh sửa Video [{index+1}]")
        edit_win.geometry("600x640")
        edit_win.resizable(False, False)
        edit_win.grab_set()  # Modal
        edit_win.focus_force()
        
        # Đặt cửa sổ giữa màn hình
        edit_win.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 600) // 2
        y = self.winfo_y() + (self.winfo_height() - 640) // 2
        edit_win.geometry(f"+{x}+{y}")
        
        main_frame = ctk.CTkScrollableFrame(edit_win, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=15, pady=10)
        
        # --- Các trường chỉnh sửa ---
        fields = {}
        
        def add_file_row(parent, label, key, filetypes=None, is_folder=False):
            row_frame = ctk.CTkFrame(parent, fg_color="transparent")
            row_frame.pack(fill="x", pady=4)
            row_frame.grid_columnconfigure(1, weight=1)
            
            lbl = ctk.CTkLabel(row_frame, text=label, font=ctk.CTkFont(weight="bold"), width=120, anchor="e")
            lbl.grid(row=0, column=0, padx=(0, 8))
            
            entry = ctk.CTkEntry(row_frame)
            entry.grid(row=0, column=1, sticky="ew", padx=(0, 5))
            entry.insert(0, task.get(key, ''))
            fields[key] = entry
            
            def browse():
                if is_folder:
                    path = filedialog.askdirectory(initialdir=os.path.dirname(task.get(key, '/')))
                else:
                    path = filedialog.askopenfilename(initialdir=os.path.dirname(task.get(key, '/')), filetypes=filetypes or [])
                if path:
                    entry.delete(0, 'end')
                    entry.insert(0, path)
            
            btn = ctk.CTkButton(row_frame, text="📂", width=36, command=browse)
            btn.grid(row=0, column=2)
        
        def add_text_row(parent, label, key, placeholder=""):
            row_frame = ctk.CTkFrame(parent, fg_color="transparent")
            row_frame.pack(fill="x", pady=4)
            row_frame.grid_columnconfigure(1, weight=1)
            
            lbl = ctk.CTkLabel(row_frame, text=label, font=ctk.CTkFont(weight="bold"), width=120, anchor="e")
            lbl.grid(row=0, column=0, padx=(0, 8))
            
            entry = ctk.CTkEntry(row_frame, placeholder_text=placeholder)
            entry.grid(row=0, column=1, sticky="ew", columnspan=2)
            entry.insert(0, task.get(key, ''))
            fields[key] = entry
        
        # Tiêu đề
        lbl_header = ctk.CTkLabel(main_frame, text=f"✏️ Chỉnh sửa Video [{index+1}]", font=ctk.CTkFont(size=18, weight="bold"), text_color="#3498db")
        lbl_header.pack(pady=(0, 10), anchor="w")
        
        add_text_row(main_frame, "Tên xuất:", "custom_name", "Để trống = tên Voice")

        # --- VOICE ROW: chọn file giọng đọc có sẵn ---
        voice_section = ctk.CTkFrame(main_frame, fg_color=("gray90", "gray17"), corner_radius=6)
        voice_section.pack(fill="x", pady=4)
        voice_file_frm = ctk.CTkFrame(voice_section, fg_color="transparent")
        voice_file_frm.pack(fill="x", padx=8, pady=8)
        voice_file_frm.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(voice_file_frm, text="🎤 Voice:", font=ctk.CTkFont(weight="bold"),
                      width=110, anchor="e").grid(row=0, column=0, padx=(0, 8))
        voice_entry_edit = ctk.CTkEntry(voice_file_frm)
        voice_entry_edit.insert(0, task.get('voice_file', ''))
        voice_entry_edit.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        def browse_voice():
            p = filedialog.askopenfilename(
                initialdir=os.path.dirname(voice_entry_edit.get() or '/'),
                filetypes=[("Audio", "*.mp3 *.wav *.m4a")]
            )
            if p:
                voice_entry_edit.delete(0, 'end')
                voice_entry_edit.insert(0, p)
        ctk.CTkButton(voice_file_frm, text="📂", width=36, command=browse_voice).grid(row=0, column=2)

        # Lưu reference cho save_changes()
        fields['_voice_file_entry'] = voice_entry_edit
        # END VOICE ROW

        add_file_row(main_frame, "Kịch bản:", "txt_file", filetypes=[("Text Script", "*.txt")])
        add_file_row(main_frame, "Thư mục Ảnh:", "img_folder", is_folder=True)
        add_file_row(main_frame, "Thư mục BGM:", "bgm_folder", is_folder=True)
        add_file_row(main_frame, "Thư mục Overlay:", "overlay_folder", is_folder=True)
        add_file_row(main_frame, "Nơi lưu:", "output_folder", is_folder=True)
        
        # Subtitle options
        lbl_sub = ctk.CTkLabel(main_frame, text="📝 Phụ đề", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f39c12")
        lbl_sub.pack(pady=(15, 5), anchor="w")
        
        sub_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        sub_frame.pack(fill="x", pady=4)
        sub_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(sub_frame, text="Font:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=0, column=0, padx=(0, 8))
        font_combo_edit = ctk.CTkOptionMenu(sub_frame, values=self.FONT_LIST)
        font_combo_edit.set(task.get('font_val', 'Georgia'))
        font_combo_edit.grid(row=0, column=1, sticky="ew")
        fields['font_val'] = font_combo_edit
        
        ctk.CTkLabel(sub_frame, text="Ngôn ngữ:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=1, column=0, padx=(0, 8))
        lang_combo_edit = ctk.CTkOptionMenu(sub_frame, values=[
            "Vietnamese", "English", "Spanish", "Portuguese", "French",
            "German", "Italian", "Japanese", "Korean", "Chinese",
            "Hindi", "Arabic", "Russian", "Indonesian", "Thai"
        ])
        lang_combo_edit.set(task.get('language', 'English'))
        lang_combo_edit.grid(row=1, column=1, sticky="ew")
        fields['language'] = lang_combo_edit

        ctk.CTkLabel(sub_frame, text="Kiểu phụ đề:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=2, column=0, padx=(0, 8), pady=(4, 0))
        sub_style_combo_edit = ctk.CTkOptionMenu(sub_frame, values=list(self.SUBTITLE_STYLE_MAP.keys()))
        sub_style_combo_edit.set(task.get('subtitle_style', 'Cổ điển'))
        sub_style_combo_edit.grid(row=2, column=1, sticky="ew", pady=(4, 0))
        fields['subtitle_style'] = sub_style_combo_edit
        
        size_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        size_frame.pack(fill="x", pady=4)
        size_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(size_frame, text="Cỡ chữ:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=0, column=0, padx=(0, 8))
        slider_fs_edit = ctk.CTkSlider(size_frame, from_=10, to=100, number_of_steps=90)
        slider_fs_edit.set(task.get('fontsize_val', 55))
        slider_fs_edit.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        lbl_fs_val = ctk.CTkLabel(size_frame, text=str(task.get('fontsize_val', 55)), width=30)
        lbl_fs_val.grid(row=0, column=2)
        slider_fs_edit.configure(command=lambda v: lbl_fs_val.configure(text=str(int(v))))
        fields['fontsize_val'] = slider_fs_edit
        
        outline_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        outline_frame.pack(fill="x", pady=4)
        outline_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(outline_frame, text="Viền:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=0, column=0, padx=(0, 8))
        slider_ol_edit = ctk.CTkSlider(outline_frame, from_=0, to=5, number_of_steps=50)
        slider_ol_edit.set(task.get('outline_val', 4.0))
        slider_ol_edit.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        lbl_ol_val = ctk.CTkLabel(outline_frame, text=f"{task.get('outline_val', 4.0):.1f}", width=30)
        lbl_ol_val.grid(row=0, column=2)
        slider_ol_edit.configure(command=lambda v: lbl_ol_val.configure(text=f"{v:.1f}"))
        fields['outline_val'] = slider_ol_edit
        
        # Volume controls
        lbl_vol = ctk.CTkLabel(main_frame, text="🔊 Âm lượng", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f39c12")
        lbl_vol.pack(pady=(15, 5), anchor="w")
        
        voice_vol_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        voice_vol_frame.pack(fill="x", pady=4)
        voice_vol_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(voice_vol_frame, text="Voice:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=0, column=0, padx=(0, 8))
        slider_voice_edit = ctk.CTkSlider(voice_vol_frame, from_=0, to=200, number_of_steps=200)
        slider_voice_edit.set(task.get('voice_vol', 100))
        slider_voice_edit.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        lbl_vv = ctk.CTkLabel(voice_vol_frame, text=f"{task.get('voice_vol', 100)}%", width=40)
        lbl_vv.grid(row=0, column=2)
        slider_voice_edit.configure(command=lambda v: lbl_vv.configure(text=f"{int(v)}%"))
        fields['voice_vol'] = slider_voice_edit
        
        bgm_vol_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        bgm_vol_frame.pack(fill="x", pady=4)
        bgm_vol_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(bgm_vol_frame, text="Nhạc nền:", font=ctk.CTkFont(weight="bold"), width=120, anchor="e").grid(row=0, column=0, padx=(0, 8))
        slider_bgm_edit = ctk.CTkSlider(bgm_vol_frame, from_=0, to=100, number_of_steps=100)
        slider_bgm_edit.set(task.get('bgm_vol', 20))
        slider_bgm_edit.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        lbl_bv = ctk.CTkLabel(bgm_vol_frame, text=f"{task.get('bgm_vol', 20)}%", width=40)
        lbl_bv.grid(row=0, column=2)
        slider_bgm_edit.configure(command=lambda v: lbl_bv.configure(text=f"{int(v)}%"))
        fields['bgm_vol'] = slider_bgm_edit

        # --- NÚT LƯU / HỦY ---
        btn_row = ctk.CTkFrame(edit_win, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(5, 15))
        
        def save_changes():
            # Nếu trong lúc mở cửa sổ này, video đã tới lượt render → không lưu nữa
            # (đổi thông số giữa chừng sẽ làm sai video đang chạy)
            if task not in self.render_queue:
                messagebox.showwarning("Không thể lưu",
                    "Video này đã bắt đầu render trong lúc bạn chỉnh sửa.\n"
                    "Thay đổi không được áp dụng.", parent=edit_win)
                edit_win.destroy()
                self.update_queue_ui()
                return

            voice = fields['_voice_file_entry'].get().strip()
            if not voice:
                messagebox.showwarning("Thiếu voice", "Vui lòng chọn file voice.", parent=edit_win)
                return

            txt = fields['txt_file'].get().strip()
            img = fields['img_folder'].get().strip()
            bgm = fields['bgm_folder'].get().strip()
            
            if not all([txt, img, bgm]):
                messagebox.showwarning("Thiếu thông tin", "Kịch bản, Ảnh và BGM không được để trống!", parent=edit_win)
                return
            
            # Xóa preprocess cache cũ
            old_key = self._get_task_key(task)
            self._preprocess_result.pop(old_key, None)
            
            # Cập nhật task
            task['custom_name'] = fields['custom_name'].get().strip()
            task['voice_file'] = voice
            task['txt_file'] = txt
            task['img_folder'] = img
            task['bgm_folder'] = bgm
            task['overlay_folder'] = fields['overlay_folder'].get().strip()
            task['output_folder'] = fields['output_folder'].get().strip()
            task['font_val'] = fields['font_val'].get()
            task['language'] = fields['language'].get()
            task['subtitle_style'] = fields['subtitle_style'].get()
            task['fontsize_val'] = int(fields['fontsize_val'].get())
            task['outline_val'] = fields['outline_val'].get()
            task['voice_vol'] = int(fields['voice_vol'].get())
            task['bgm_vol'] = int(fields['bgm_vol'].get())

            self.update_queue_ui()
            edit_win.destroy()
        
        btn_save = ctk.CTkButton(btn_row, text="💾 LƯU THAY ĐỔI", fg_color="#27ae60", hover_color="#2ecc71",
                                  font=ctk.CTkFont(size=14, weight="bold"), height=40, command=save_changes)
        btn_save.pack(side="left", expand=True, fill="x", padx=(0, 5))
        
        btn_cancel = ctk.CTkButton(btn_row, text="❌ HỦY", fg_color="#7f8c8d", hover_color="#95a5a6",
                                    font=ctk.CTkFont(size=14, weight="bold"), height=40, command=edit_win.destroy)
        btn_cancel.pack(side="left", expand=True, fill="x", padx=(5, 0))

    def add_to_queue(self):
        # Validate dựa trên render_mode
        if self.render_mode == "jesus_split":
            # Mode video nền: cần thư mục video nền + kịch bản.
            # Các thành phần (chân dung/logo/…) là TÙY CHỌN qua composer.
            if not self.txt_file:
                messagebox.showwarning("Thiếu thông tin", "Vui lòng chọn Kịch bản (TXT)!")
                return
            if not (self.bg_video_folder and os.path.isdir(self.bg_video_folder)):
                messagebox.showwarning("Thiếu video nền",
                    "Hãy chọn Thư mục Videos nền.")
                return
            vids = [f for f in os.listdir(self.bg_video_folder)
                    if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))]
            if not vids:
                messagebox.showwarning("Thiếu video nền",
                    f"Thư mục '{os.path.basename(self.bg_video_folder)}' không có file video (.mp4/.mov/...)!")
                return
        else:
            # Mode Ken Burns gốc
            if not all([self.img_folder, self.bgm_folder, self.txt_file]):
                messagebox.showwarning("Thiếu thông tin", "Vui lòng chọn đầy đủ Ảnh, Kịch bản và Thư mục Nhạc!")
                return

        if not self.voice_file:
            messagebox.showwarning("Thiếu voice", "Vui lòng chọn file voice (.mp3/.wav).")
            return

        # Pre-validate optional files — warning chứ không block (sẽ tự skip khi render)
        missing_optional = []
        if self.sfx_wipe_file and not os.path.exists(self.sfx_wipe_file):
            missing_optional.append(f"SFX wipe: {self.sfx_wipe_file}")
        if self.sfx_blink_file and not os.path.exists(self.sfx_blink_file):
            missing_optional.append(f"SFX blink: {self.sfx_blink_file}")
        if self.overlay_folder and not os.path.exists(self.overlay_folder):
            missing_optional.append(f"Thư mục Overlay: {self.overlay_folder}")
        if missing_optional:
            self.log("⚠ Các file/thư mục không tồn tại (sẽ bỏ qua khi render):")
            for item in missing_optional:
                self.log(f"   • {item}")

        task = {
            "img_folder": self.img_folder,
            "voice_file": self.voice_file,
            "bgm_folder": self.bgm_folder,
            "txt_file": self.txt_file, "overlay_folder": self.overlay_folder,
            "overlay_file": self.overlay_specific_file,
            "sfx_wipe_file": self.sfx_wipe_file, "sfx_blink_file": self.sfx_blink_file,
            "output_folder": self.output_folder, "outline_val": self.slider_outline.get(), 
            "fontsize_val": int(self.slider_fontsize.get()), "font_val": self.font_combo.get(),
            "language": self.lang_combo.get(),
            "subtitle_style": self.subtitle_style_combo.get(),
            # Tùy chỉnh chữ (CapCut style)
            "sub_case": self.sub_case, "sub_text_color": self.sub_text_color,
            "sub_outline_color": self.sub_outline_color, "sub_glow": self.sub_glow,
            # Chỉnh ánh sáng + effect nền video
            "bg_brightness": self.bg_brightness, "bg_contrast": self.bg_contrast,
            "bg_saturation": self.bg_saturation, "bg_effect": self.bg_effect,
            "ratio_name": "16:9 (YouTube/Ngang)",
            "voice_vol": int(self.slider_voice_vol.get()),
            "bgm_vol": int(self.slider_bgm_vol.get()),
            "custom_name": self._safe_filename(self.entry_vid_name.get()),
            # RENDER MODE fields
            "render_mode": self.render_mode,
            "bg_video_folder": self.bg_video_folder if self.render_mode == "jesus_split" else "",
            # Chỉnh nền video (zoom + dịch khung) — chốt tại thời điểm thêm vào hàng đợi
            "bg_zoom": self.bg_zoom, "bg_off_x": self.bg_off_x, "bg_off_y": self.bg_off_y,
            # COMPOSER (dùng cho CẢ 2 mode): layer overlay + ô phụ đề kéo-thả
            "overlay_layers": [dict(l) for l in self.overlay_layers],
            "kb_text_layout": dict(self.kb_text_layout),
        }
        self.render_queue.append(task)
        self.entry_vid_name.delete(0, 'end')
        self.update_queue_ui()
        self.btn_add_queue.configure(text="✅ ĐÃ THÊM THÀNH CÔNG")
        self.after(1500, lambda: self.btn_add_queue.configure(text="🚀 THÊM VÀO HÀNG ĐỢI & RENDER"))
        # TỰ ĐỘNG render ngay: đang rảnh → khởi động; đang render → video này tự nối đuôi
        if not self.is_rendering:
            self.screen_tabs.set(self.SCREEN_OUTPUT)   # chuyển sang màn theo dõi xuất video
            self._start_render_engine()
        else:
            self.log(f"➕ Đã nối vào hàng chờ ({len(self.render_queue)} video đang đợi).")

    def _show_copyable_error(self, title: str, summary: str, details: str = "",
                              show_export_bundle: bool = True):
        """Hiển thị popup lỗi với textbox cho phép user SELECT/COPY chi tiết.
        Khác với messagebox.showerror (text read-only không copy được).
        
        Args:
            title: Tiêu đề cửa sổ
            summary: Tóm tắt ngắn (hiện ở đầu)
            details: Traceback / chi tiết kỹ thuật (hiện trong textbox)
            show_export_bundle: Có hiện nút "Xuất Debug Bundle" không
        """
        err_win = ctk.CTkToplevel(self)
        err_win.title(title)
        err_win.geometry("780x520")
        err_win.transient(self)
        err_win.grab_set()
        err_win.lift()
        err_win.focus_force()
        
        # Header
        header = ctk.CTkFrame(err_win, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(
            header, text="⚠️", font=ctk.CTkFont(size=32)
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            header, text=title,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#f39c12"
        ).pack(side="left")
        
        # Summary
        sum_lbl = ctk.CTkLabel(
            err_win, text=summary,
            font=ctk.CTkFont(size=13),
            justify="left", wraplength=720, anchor="w"
        )
        sum_lbl.pack(fill="x", padx=20, pady=(0, 10))
        
        # Details textbox (selectable, copyable)
        if details:
            ctk.CTkLabel(
                err_win, text="Chi tiết kỹ thuật (chọn text để copy):",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="gray", anchor="w"
            ).pack(fill="x", padx=20, pady=(5, 2))
            
            txt = ctk.CTkTextbox(
                err_win,
                height=230,
                font=ctk.CTkFont(family="Consolas", size=11),
                wrap="word"
            )
            txt.pack(fill="both", expand=True, padx=20, pady=(0, 10))
            txt.insert("0.0", details)
            # KHÔNG configure(state="disabled") — để user select copy được
        
        # Buttons
        btn_frame = ctk.CTkFrame(err_win, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        def copy_details():
            """Copy toàn bộ chi tiết vào clipboard."""
            try:
                full_text = f"{title}\n\n{summary}\n\n{'='*60}\n{details}"
                err_win.clipboard_clear()
                err_win.clipboard_append(full_text)
                err_win.update()
                btn_copy.configure(text="✅ Đã copy!", fg_color="#27ae60")
                err_win.after(1500, lambda: btn_copy.configure(
                    text="📋 Copy chi tiết lỗi", fg_color=["#3a7ebf", "#1f538d"]
                ))
            except Exception as ex:
                btn_copy.configure(text=f"Lỗi copy: {ex}")
        
        btn_copy = ctk.CTkButton(
            btn_frame, text="📋 Copy chi tiết lỗi",
            command=copy_details, height=36, width=180
        )
        btn_copy.pack(side="left", padx=(0, 8))
        
        if show_export_bundle and hasattr(self, '_export_debug_bundle_action'):
            ctk.CTkButton(
                btn_frame, text="📤 Xuất Debug Bundle",
                command=lambda: self._export_debug_bundle_action(),
                height=36, width=180, fg_color="#8e44ad", hover_color="#7d3c98"
            ).pack(side="left", padx=(0, 8))
        
        ctk.CTkButton(
            btn_frame, text="Đóng", command=err_win.destroy,
            height=36, width=100, fg_color="gray30", hover_color="gray40"
        ).pack(side="right")

    def _start_render_engine(self):
        """Khởi động bộ render (tự gọi khi THÊM VÀO HÀNG ĐỢI — không còn nút riêng)."""
        if self.is_rendering: return
        if not self.render_queue: return

        # Helper: format full traceback
        def _build_details(e):
            import traceback as _tb
            return _tb.format_exc()
        
        try:
            import stable_whisper
        except ImportError as e:
            err_msg = str(e)
            self.log(f"❌ ImportError: {err_msg}")
            
            if DEBUG_LOGGER_AVAILABLE:
                try:
                    debug_logger.save_render_failure(
                        operation="stable_whisper_import",
                        exception=e,
                        extra_context={"action": "start_queue", "step": "import_stable_whisper"}
                    )
                except Exception:
                    pass
            
            # Phân tích module nào thiếu để gợi ý
            hint = ""
            low = err_msg.lower()
            if "torch.distributed" in low:
                hint = "Build EXE đã exclude torch.distributed nhưng stable_whisper cần nó.\n→ Cần rebuild EXE với app.spec mới (bỏ excludes torch.*)."
            elif "torch" in low:
                hint = "Module torch hoặc submodule bị thiếu trong EXE build.\n→ Cần rebuild EXE với spec đầy đủ hidden imports."
            elif "whisper" in low:
                hint = "Module whisper bị thiếu hoặc lỗi import.\n→ Cần check whisper data assets trong EXE build."
            else:
                hint = "Module bị thiếu trong EXE build."
            
            self._show_copyable_error(
                title="Lỗi import thư viện AI",
                summary=(
                    f"Không import được thư viện cần thiết khi khởi tạo render.\n\n"
                    f"🔍 Module thiếu: {err_msg}\n\n"
                    f"💡 {hint}"
                ),
                details=_build_details(e)
            )
            return
        except (OSError, RuntimeError) as e:
            err_msg = str(e)
            self.log(f"❌ {type(e).__name__}: {err_msg[:200]}")
            
            if DEBUG_LOGGER_AVAILABLE:
                try:
                    debug_logger.save_render_failure(
                        operation="stable_whisper_import",
                        exception=e,
                        extra_context={"action": "start_queue", "step": "import_stable_whisper"}
                    )
                except Exception:
                    pass
            
            # Phát hiện DLL nào thiếu
            hint = ""
            low = err_msg.lower()
            if "cublas" in low and ".dll" in low:
                hint = ("Thiếu CUDA DLL (cublas64 / cublasLt64).\n"
                        "→ Build EXE bị skip nhầm. Mở app.spec → kiểm tra SKIP_CUDA_DLLS.")
            elif "cudart" in low:
                hint = "Thiếu cudart64_12.dll (CUDA runtime). Cần build lại."
            elif "shm" in low:
                hint = "Thiếu shm.dll hoặc dependency của nó. Thường do thiếu CUDA libs khác."
            elif ".dll" in low:
                hint = "Một DLL của PyTorch bị thiếu trong EXE build."
            else:
                hint = "Thư viện AI khởi tạo thất bại."
            
            self._show_copyable_error(
                title="Lỗi khởi tạo PyTorch / CUDA",
                summary=(
                    f"Không khởi động được thư viện xử lý voice.\n\n"
                    f"🔍 {type(e).__name__}: {err_msg[:300]}\n\n"
                    f"💡 {hint}"
                ),
                details=_build_details(e)
            )
            return
        except Exception as e:
            err_msg = str(e)
            self.log(f"❌ Lỗi không xác định: {type(e).__name__}: {err_msg[:200]}")
            
            if DEBUG_LOGGER_AVAILABLE:
                try:
                    debug_logger.save_render_failure(
                        operation="stable_whisper_import",
                        exception=e,
                        extra_context={"action": "start_queue"}
                    )
                except Exception:
                    pass
            
            self._show_copyable_error(
                title="Lỗi không xác định",
                summary=(
                    f"Xảy ra lỗi không lường trước khi khởi tạo thư viện AI.\n\n"
                    f"🔍 {type(e).__name__}: {err_msg[:400]}\n\n"
                    f"💡 Vui lòng gửi 'Debug Bundle' cho developer."
                ),
                details=_build_details(e)
            )
            return
        
        # --- KIỂM TRA DUNG LƯỢNG Ổ LƯU VIDEO (video dài dễ đầy ổ → hỏng file ở 100%) ---
        try:
            import shutil as _sh
            _t0 = self.render_queue[0]
            _out_dir = _t0.get('output_folder') or os.path.dirname(_t0.get('voice_file', '') or APP_DIR)
            if not os.path.isdir(_out_dir):
                _out_dir = APP_DIR
            _free_gb = _sh.disk_usage(_out_dir).free / (1024 ** 3)
            if _free_gb < 15:
                if not messagebox.askyesno(
                        "⚠️ Ổ đĩa sắp đầy",
                        f"Ổ lưu video chỉ còn trống {_free_gb:.1f} GB.\n\n"
                        f"Video dài (1-3 giờ) có thể nặng 4-12 GB — nếu hết chỗ giữa chừng,\n"
                        f"FFmpeg sẽ lỗi ở bước cuối và file hỏng (đã từng xảy ra).\n\n"
                        f"Bạn có muốn TIẾP TỤC render không?"):
                    return
            self.log(f"💾 Ổ lưu video còn trống: {_free_gb:.1f} GB")
        except Exception:
            pass

        self.is_rendering = True; self.cancel_render = False
        # Tạm dừng live preview khi render — nhường CPU cho FFmpeg (bấm ▶ để bật lại)
        self._pv_pause()
        self.btn_cancel_render.pack(fill="x", padx=10, pady=(0, 5))
        self.start_next_task()
        
    # ============================================================
    # RENDER MONITOR — hiển thị chi tiết stage hiện tại
    # ============================================================
    # Config cho từng stage: title, icon, color theme
    _STAGE_CONFIG = {
        "whisper": {
            "title": "PHÂN TÍCH KỊCH BẢN",
            "icon": "🧠",
            "step": "Bước 1/2",
            "bg": "#1a2d3d",      # Xanh dương đậm
            "accent": "#5dade2",
            "title_color": "#85c1e2",
        },
        "video": {
            "title": "RENDER VIDEO",
            "icon": "🎬",
            "step": "Bước 2/2",
            "bg": "#1e2e1e",      # Xanh lá đậm
            "accent": "#82e0aa",
            "title_color": "#7dcea0",
        },
        "cid": {
            "title": "ĐÓNG GÓI VIDEO CUỐI",
            "icon": "🎞️",
            "step": "Bước cuối",
            "bg": "#3d2a1a",      # Cam đậm
            "accent": "#f8c471",
            "title_color": "#eb984e",
        },
    }

    def set_render_stage(self, stage_key: str, detail: str, overall_pct: float = None):
        """Cập nhật stage hiện tại — sẽ được hiển thị trên monitor preview.
        
        Args:
            stage_key: 'whisper' | 'video' | 'cid'
            detail: mô tả chi tiết (vd: "Sinh chunk 8/12: 'The Lord guides...'")
            overall_pct: tổng tiến độ [0..1], None = không update
        """
        self._render_stage = stage_key
        self._render_stage_detail = detail
        if overall_pct is not None:
            self._render_overall_pct = overall_pct
        # Đồng thời update thanh progress dưới
        cfg = self._STAGE_CONFIG.get(stage_key, {})
        prefix = f"{cfg.get('step', '')}: " if cfg else ""
        if overall_pct is not None:
            self.update_progress_ui(overall_pct, f"{prefix}{detail}")
        else:
            self.update_progress_ui(self._render_overall_pct, f"{prefix}{detail}")

    def _show_render_monitor(self):
        """Hiện khung màn hình render (trên màn 📊 XUẤT VIDEO, ngay dưới tiến độ)."""
        if not self.render_monitor_visible:
            try:
                self.render_monitor_frame.pack(fill="x", padx=10, pady=(0, 4),
                                               after=self.lbl_live_progress)
            except Exception:
                self.render_monitor_frame.pack(fill="x", padx=10, pady=(0, 4))
            self.render_monitor_visible = True

    def _hide_render_monitor(self):
        """Ẩn khung màn hình render khi đã xong/hủy."""
        if self.render_monitor_visible:
            self.render_monitor_frame.pack_forget()
            self.render_monitor_visible = False

    def _set_render_status(self, text, color="#1e5228"):
        """Hiển thị trạng thái kết thúc (✅/❌) trên khung render riêng — KHÔNG chiếm preview."""
        if threading.current_thread() is not threading.main_thread():
            try: self.after(0, self._set_render_status, text, color)
            except Exception: pass
            return
        self._show_render_monitor()
        try:
            self.render_monitor_frame.configure(fg_color=color)
            self.lbl_render_monitor.configure(text=text, text_color="#ffffff")
        except Exception:
            pass

    def animate_rendering_screen(self):
        if not self.is_rendering:
            return

        self._show_render_monitor()

        if not hasattr(self, 'anim_frame_count'): self.anim_frame_count = 0

        spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner = spinners[self.anim_frame_count % len(spinners)]

        # Tên video đang render
        display_name = ""
        if self.current_task:
            custom_name = self.current_task.get('custom_name', '').strip()
            if custom_name:
                safe_name = self._safe_filename(custom_name)
                display_name = f"{safe_name}.mp4"
            else:
                if self.current_task.get('voice_file'):
                    voice_basename = os.path.splitext(os.path.basename(self.current_task['voice_file']))[0]
                else:
                    voice_basename = "video"
                display_name = f"FINAL_{voice_basename}.mp4"

        # Lấy config theo stage hiện tại (fallback nếu idle)
        cfg = self._STAGE_CONFIG.get(self._render_stage, {
            "title": "ĐANG XỬ LÝ", "icon": "⚙️", "step": "",
            "bg": "#1a1a1a", "accent": "#aaaaaa", "title_color": "#fffb00",
        })

        pct = max(0.0, min(self._render_overall_pct, 1.0))
        pct_str = f"{int(pct * 100)}%"

        elapsed_str = ""
        if self._render_start_time:
            elapsed = time.time() - self._render_start_time
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            elapsed_str = f"{mins}m {secs:02d}s" if mins > 0 else f"{secs}s"

        detail = self._render_stage_detail or "Đang khởi động..."
        if len(detail) > 64:
            detail = detail[:61] + "..."

        # Khối hiển thị COMPACT (2 dòng) — đủ thông tin nhưng không chiếm chỗ
        display_text = (
            f"{cfg['icon']} {cfg['title']} · {cfg['step']} · {pct_str}  (⏱ {elapsed_str})\n"
            f"[{spinner}] {detail}"
        )
        if display_name:
            display_text += f"\n📽 {display_name}"

        try:
            self.render_monitor_frame.configure(fg_color=cfg["bg"])
            self.lbl_render_monitor.configure(text=display_text, text_color=cfg["title_color"])
        except Exception:
            pass

        self.anim_frame_count += 1
        self.after(200, self.animate_rendering_screen)
    

    def start_next_task(self):
        if self.cancel_render: return
        if not self.render_queue:
            self.is_rendering = False
            self.btn_cancel_render.pack_forget()
            self.update_progress_ui(1.0, "Đã hoàn thành toàn bộ hàng đợi!")
            self._hide_render_monitor()
            self.log("\n" + "="*40 + "\n✅ TẤT CẢ VIDEO TRONG HÀNG ĐỢI ĐÃ XUẤT XONG!\n" + "="*40)
            messagebox.showinfo("Thành công", "Tuyệt vời! Toàn bộ hàng đợi đã được xuất thành công!")
            return

        self.current_task = self.render_queue.pop(0)
        self.update_queue_ui()
        self.progress_bar.set(0); self.lbl_percent.configure(text="0%")
        self.log_box.configure(state="normal"); self.log_box.delete("1.0", "end"); self.log_box.configure(state="disabled")

        voice_name = os.path.basename(self.current_task.get('voice_file', '')) or "(unknown)"
        initial_stage = "whisper"
        initial_detail = "Chuẩn bị phân tích kịch bản..."
        self.log(f"🚀 BẮT ĐẦU XỬ LÝ VIDEO GỐC: {voice_name}")

        # Reset stage state cho task mới
        self._render_start_time = time.time()
        self._render_overall_pct = 0.0
        self.set_render_stage(initial_stage, initial_detail, overall_pct=0.0)

        self.animate_rendering_screen()

        threading.Thread(target=self.process_video, daemon=True).start()

    # --- FONT LIST (có sẵn trên Windows 10/11, hầu hết hỗ trợ dấu tiếng Việt) ---
    FONT_LIST = [
        # Serif — sang trọng, cổ điển
        "Georgia", "Palatino Linotype", "Garamond", "Times New Roman",
        "Book Antiqua", "Cambria", "Constantia", "Sitka Heading",
        # Sans-serif — hiện đại, dễ đọc
        "Arial", "Arial Black", "Verdana", "Tahoma", "Trebuchet MS",
        "Segoe UI", "Segoe UI Black", "Segoe UI Semibold",
        "Calibri", "Candara", "Corbel", "Bahnschrift", "Franklin Gothic Medium",
        # Display — đậm, bắt mắt (Impact KHÔNG có dấu tiếng Việt)
        "Impact", "Comic Sans MS", "Gabriola",
        # Monospace
        "Consolas", "Courier New",
    ]

    # --- LANGUAGE SUPPORT ---
    LANG_MAP = {
        "Vietnamese": "vi", "English": "en", "Spanish": "es", "Portuguese": "pt",
        "French": "fr", "German": "de", "Italian": "it", "Japanese": "ja",
        "Korean": "ko", "Chinese": "zh", "Hindi": "hi", "Arabic": "ar",
        "Russian": "ru", "Indonesian": "id", "Thai": "th"
    }

    # --- SUBTITLE STYLE MAP ---
    SUBTITLE_STYLE_MAP = {
        "Cổ điển": "classic",
        "Karaoke": "karaoke",
        "Word Reveal": "word_reveal",
        "Holy Light": "holy_light",
        "Divine Glow": "divine_glow",
        # 5 template phân tích từ video mẫu
        "Nhấn Mạnh (2 chữ)": "tpl_pop2",
        "Cuốn Vàng (3 chữ)": "tpl_karaoke3",
        "Hiện Dần Câu": "tpl_sentence",
        "Phóng To Theo Lời": "tpl_pophl",
        "Câu + Keyword Vàng": "tpl_keyword",
        # 10 kiểu mới (v1.6.2)
        "Máy Đánh Chữ": "typewriter",
        "Nảy Từng Từ": "bounce",
        "Cụm 4 Chữ": "chunk4",
        "Hộp Nền Đen": "boxed",
        "Tối Giản Mềm": "minimal",
        "Điện Ảnh": "cinematic",
        "Neon Xanh": "neon",
        "Pop Bóng Đổ": "shadow_pop",
        "Viền Vàng Kép": "double_gold",
        "Vàng Hoàng Gia": "royal_gold",
    }
    
    GLOW_KEYWORDS = {
        "vi": ['chúa', 'thượng đế', 'phước', 'phép lạ', 'cứu rỗi', 'thiên đường', 'amen', 'sứ mệnh', 'ánh sáng', 'chiến thắng'],
        "en": ['god', 'lord', 'jesus', 'chosen', 'destiny', 'breakthrough', 'miracle', 'warning', 'urgent', 'victory', 'light', 'listen'],
        "es": ['dios', 'señor', 'jesús', 'elegido', 'destino', 'milagro', 'victoria', 'luz', 'bendición', 'escucha'],
        "pt": ['deus', 'senhor', 'jesus', 'escolhido', 'destino', 'milagre', 'vitória', 'luz', 'bênção', 'ouça'],
        "fr": ['dieu', 'seigneur', 'jésus', 'élu', 'destin', 'miracle', 'victoire', 'lumière', 'bénédiction', 'écoute'],
        "de": ['gott', 'herr', 'jesus', 'auserwählt', 'schicksal', 'wunder', 'sieg', 'licht', 'segen', 'hör'],
        "it": ['dio', 'signore', 'gesù', 'eletto', 'destino', 'miracolo', 'vittoria', 'luce', 'benedizione', 'ascolta'],
        "ja": ['神', '主', 'イエス', '運命', '奇跡', '勝利', '光', '祝福', '聞いて'],
        "ko": ['하나님', '주님', '예수', '운명', '기적', '승리', '빛', '축복', '들어'],
        "zh": ['上帝', '主', '耶稣', '命运', '奇迹', '胜利', '光', '祝福', '听'],
        "hi": ['भगवान', 'प्रभु', 'यीशु', 'भाग्य', 'चमत्कार', 'जीत', 'प्रकाश', 'आशीर्वाद', 'सुनो'],
        "ar": ['الله', 'الرب', 'يسوع', 'القدر', 'معجزة', 'نصر', 'نور', 'بركة', 'استمع'],
        "ru": ['бог', 'господь', 'иисус', 'судьба', 'чудо', 'победа', 'свет', 'благословение', 'слушай'],
        "id": ['tuhan', 'yesus', 'takdir', 'mukjizat', 'kemenangan', 'cahaya', 'berkat', 'dengar'],
        "th": ['พระเจ้า', 'พระเยซู', 'โชคชะตา', 'ปาฏิหาริย์', 'ชัยชนะ', 'แสงสว่าง', 'พร', 'ฟัง'],
    }

    def _get_lang_code(self, lang_name):
        return self.LANG_MAP.get(lang_name, 'en')

    def highlight_blackscreen_keyword(self, text, lang_code='en'):
        words = text.split()
        if not words: return text
        keywords = self.GLOW_KEYWORDS.get(lang_code, self.GLOW_KEYWORDS['en'])
        target_idx = len(words) - 1 
        for i, w in enumerate(words):
            clean_w = re.sub(r'\W+', '', w.lower()) 
            if clean_w in keywords: target_idx = i; break
        target_word = words[target_idx]
        glow_style = f"{{\\c&H0000FF&\\3c&H00FFFF&\\bord4\\blur4\\u1}}{target_word}{{\\r}}"
        words[target_idx] = glow_style
        return " ".join(words)

    def write_ass_subtitle(self, subtitle_events, font_name, font_size, outline, filepath, res_x, res_y, lang_code='en', style_mode='classic', layout='center', text_box=None):
        """Generate ASS subtitle với 5 style:
          - classic      : cả câu hiện 1 lần (mặc định)
          - karaoke      : cả câu hiện, đổi màu khi voice đọc tới (\\kf sweep)
          - word_reveal  : từng từ fade-in tuần tự, VỊ TRÍ CỐ ĐỊNH (không reflow)
          - holy_light   : từng từ vật chất hoá từ blur+viền vàng → sharp+viền đen
          - divine_glow  : SPOTLIGHT - mỗi từ vàng + glow khi voice đọc tới, sau đó
                           trở lại trắng bình thường (chỉ 1 từ highlight tại 1 thời điểm)

        layout:
          - 'center'    : căn giữa (mode Ken Burns gốc) — MarginL/R = 10, Alignment 5
          - 'right_box' : đặt theo khung chữ (mode Jesus). Nếu có text_box={x,y,w}
                          (phân số) → dùng Alignment 8 (neo trên, căn giữa ngang) +
                          margins theo khung; nếu không → mặc định nửa phải.

        Segment kết thúc (is_segment_ending=True) LUÔN giữ style cũ để không
        phá vỡ màn hình đen + keyword glow."""
        # --- MARGIN + ALIGNMENT theo layout ---
        _box_center = None   # (cx,cy) px để căn chữ GIỮA khung (cả dọc + ngang)
        if layout == 'right_box':
            if text_box:
                _bx = text_box.get('x', 0.50); _bw = text_box.get('w', 0.46)
                _by = text_box.get('y', 0.34); _bh = text_box.get('h', None)
                m_l = int(res_x * _bx)
                m_r = int(res_x * (1.0 - _bx - _bw))
                m_v = int(res_y * _by)
                if _bh:
                    # Có chiều cao khung → căn GIỮA cả dọc lẫn ngang bằng \an5\pos(tâm khung)
                    _box_center = (int(res_x * (_bx + _bw / 2.0)),
                                   int(res_y * (_by + _bh / 2.0)))
            else:
                m_l = int(res_x * 0.50); m_r = int(res_x * 0.04); m_v = int(res_y * 0.34)
            m_r = max(m_r, 10)
            align = 8  # neo TRÊN, căn giữa ngang → di chuyển dọc được qua MarginV, vẫn auto-wrap
        else:
            m_l, m_r, m_v, align = 10, 10, 100, 5

        # --- STYLES ---
        # Default: PrimaryColour=trắng (chữ thường), SecondaryColour=vàng (không dùng nhiều)
        main_style = (
            f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,{align},{m_l},{m_r},{m_v},1"
        )
        # Karaoke: PrimaryColour=vàng (đã đọc), SecondaryColour=trắng (chưa đọc)
        karaoke_style = (
            f"Style: Karaoke,{font_name},{font_size},&H0000FFFF,&H00FFFFFF,"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,{align},{m_l},{m_r},{m_v},1"
        )
        # Boxed: BorderStyle=3 → hộp nền đen mờ sau chữ (kiểu caption tin tức/podcast)
        boxed_style = (
            f"Style: Boxed,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,"
            f"&H96000000,&H96000000,-1,0,0,0,100,100,0,0,3,{max(outline, 2)},0,{align},{m_l},{m_r},{m_v},1"
        )

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{main_style}
{karaoke_style}
{boxed_style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(header)

            for ev in subtitle_events:
                # BLACKSCREEN: luôn dùng logic cũ (keyword glow).
                # Đánh dấu Effect="BS" → hậu xử lý KHÔNG căn-giữa-khung dòng này
                # (màn hình đen phải giữ căn giữa MÀN HÌNH, không theo khung chữ).
                if ev['is_segment_ending']:
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    text = self.highlight_blackscreen_keyword(ev['text'], lang_code)
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,BS,{text}\n")
                    continue

                # ============================================================
                # 7 KIỂU CẢ CÂU (v1.6.2) — không cần word timestamps
                # ============================================================
                _WHOLE_LINE_STYLES = {
                    # style ASS dùng, tag mở đầu
                    'boxed':       ("Boxed",   "{\\fad(120,100)}"),
                    'minimal':     ("Default", f"{{\\bord{max(outline * 0.5, 1.0):.1f}\\shad0\\fad(160,140)}}"),
                    'cinematic':   ("Default", f"{{\\fsp3\\blur1\\shad2\\4c&H000000&\\fad(350,300)}}"),
                    'neon':        ("Default", f"{{\\3c&HFFFF00&\\bord{outline:.1f}\\blur4\\fad(150,130)}}"),
                    'shadow_pop':  ("Default", "{\\shad4\\4c&H000000&\\fscx82\\fscy82\\t(0,130,\\fscx100\\fscy100)}"),
                    'double_gold': ("Default", f"{{\\3c&H00D7FF&\\bord{outline:.1f}\\shad3\\4c&H000000&}}"),
                    'royal_gold':  ("Default", "{\\1c&H00D7FF&\\3c&H000000&\\blur0.5\\fad(140,120)}"),
                }
                if style_mode in _WHOLE_LINE_STYLES:
                    _sty, _pre = _WHOLE_LINE_STYLES[style_mode]
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},{_sty},,0,0,0,,{_pre}{ev['text']}\n")
                    continue

                words = ev.get('words') or []

                # FALLBACK: classic hoặc thiếu word timestamps
                if not words or style_mode == 'classic':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{ev['text']}\n")
                    continue

                # KARAOKE: \kf fill cả câu (chưa đọc=trắng → đọc rồi=vàng)
                if style_mode == 'karaoke':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    parts = []
                    first_gap = words[0]['start'] - ev['start']
                    if first_gap > 0.05:
                        parts.append(f"{{\\k{int(first_gap * 100)}}}")
                    for w in words:
                        dur_cs = max(int((w['end'] - w['start']) * 100), 1)
                        parts.append(f"{{\\kf{dur_cs}}}{w['word']}")
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Karaoke,,0,0,0,,{' '.join(parts)}\n")
                    continue

                # MÁY ĐÁNH CHỮ: từng từ hiện TỨC THÌ khi voice đọc tới (không fade)
                # Cùng kỹ thuật alpha-only như word_reveal → không bao giờ nhảy chỗ
                if style_mode == 'typewriter':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    seg_start = ev['start']
                    parts = []
                    for w in words:
                        t_in = max(int((w['start'] - seg_start) * 1000), 0)
                        parts.append(
                            f"{{\\alpha&HFF&\\t({t_in},{t_in + 10},\\alpha&H00&)}}{w['word']}")
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{' '.join(parts)}\n")
                    continue

                # NẢY TỪNG TỪ: từ hiện + nảy DỌC (fscy 55→100) — chỉ scale chiều cao
                # nên bề rộng không đổi → không reflow, không nhảy chỗ
                if style_mode == 'bounce':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    seg_start = ev['start']
                    parts = []
                    for w in words:
                        t_in = max(int((w['start'] - seg_start) * 1000), 0)
                        parts.append(
                            f"{{\\alpha&HFF&\\fscy55"
                            f"\\t({t_in},{t_in + 220},0.4,\\alpha&H00&\\fscy100)}}{w['word']}")
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{' '.join(parts)}\n")
                    continue

                # CỤM 4 CHỮ: hiện theo cụm 4 từ theo lời, trắng sạch + pop nhẹ
                if style_mode == 'chunk4':
                    seg_end = min(ev['end'] + 0.3, ev['scene_end'])
                    chunks = [words[i:i + 4] for i in range(0, len(words), 4)]
                    for ci, chunk in enumerate(chunks):
                        c_start = chunk[0]['start']
                        c_end = chunks[ci + 1][0]['start'] if ci + 1 < len(chunks) else seg_end
                        c_end = max(c_end, c_start + 0.15)
                        txt = ' '.join(w['word'] for w in chunk)
                        lead = "{\\fscx88\\fscy88\\t(0,110,\\fscx100\\fscy100)}"
                        f.write(f"Dialogue: 0,{self.format_ass_time(c_start)},"
                                f"{self.format_ass_time(c_end)},Default,,0,0,0,,{lead}{txt}\n")
                    continue

                # WORD REVEAL: từng từ fade-in MƯỢT, VỊ TRÍ CỐ ĐỊNH (không reflow)
                if style_mode == 'word_reveal':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    seg_start = ev['start']
                    parts = []
                    for idx, w in enumerate(words):
                        delay_ms = max(int((w['start'] - seg_start) * 1000), 0)
                        t_in = delay_ms
                        t_out = t_in + 280   # 280ms fade
                        # CHỈ animate alpha (FF→00). KHÔNG scale chữ (\fscx) vì scale làm
                        # đổi bề rộng → cả dòng re-wrap + căn giữa lại → chữ trước bị xê dịch.
                        # Alpha-only: mọi chữ chiếm đủ chỗ ngay từ đầu (dù còn vô hình) nên
                        # khi hiện ra là ĐÚNG vị trí cuối cùng, không bao giờ nhảy chỗ.
                        parts.append(
                            f"{{\\alpha&HFF&\\t({t_in},{t_out},0.6,\\alpha&H00&)}}{w['word']}"
                        )
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{' '.join(parts)}\n")
                    continue

                # HOLY LIGHT: từng từ "vật chất hoá" từ blur + viền vàng → sharp + viền đen
                if style_mode == 'holy_light':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    seg_start = ev['start']
                    parts = []
                    for w in words:
                        t_in = max(int((w['start'] - seg_start) * 1000), 0)
                        t_out = t_in + 350
                        # Initial: invisible, blur=6, viền vàng
                        # End: visible, blur=0, viền đen — accel=0.5 ease-out mạnh
                        parts.append(
                            f"{{\\alpha&HFF&\\blur6\\3c&H0000FFFF&"
                            f"\\t({t_in},{t_out},0.5,\\alpha&H00&\\blur0\\3c&H00000000&)"
                            f"}}{w['word']}"
                        )
                    text = ' '.join(parts)
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}\n")
                    continue

                # DIVINE GLOW (spotlight): cả câu hiện trắng từ đầu, voice đọc tới
                # từ NÀO thì từ ĐÓ vàng + glow viền + blur nhẹ trong lúc đang đọc,
                # sau khi voice qua → từ đó trở lại trắng bình thường.
                # Chỉ MỘT từ được highlight tại mỗi thời điểm (spotlight effect).
                #
                # CRITICAL: ASS \t() tags carry-forward sang các từ sau trong cùng
                # dialogue line. Nếu không có \r reset, \t(0, 80, vàng) của từ đầu
                # sẽ áp dụng cho TẤT CẢ từ → cả câu chuyển vàng cùng lúc.
                # Dùng \r ở đầu mỗi từ (trừ từ đầu) để CANCEL các animation đã
                # schedule từ block trước, sau đó schedule animation riêng cho từ này.
                if style_mode == 'divine_glow':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    seg_start = ev['start']
                    # Thời gian transition vào/ra trạng thái glow (ms)
                    FADE_IN_MS = 80
                    FADE_OUT_MS = 120
                    parts = []
                    for i, w in enumerate(words):
                        t_word_start = max(int((w['start'] - seg_start) * 1000), 0)
                        t_word_end = max(int((w['end'] - seg_start) * 1000), t_word_start + 50)
                        # 2 transition:
                        #   1) IN: trắng → vàng + viền trắng glow + blur 3
                        #   2) OUT: vàng/glow → trắng bình thường
                        in_anim = (
                            f"\\t({t_word_start},{t_word_start+FADE_IN_MS},"
                            f"\\1c&H00FFFF&\\3c&HFFFFFF&\\bord{outline+3:.1f}\\blur3)"
                        )
                        out_anim = (
                            f"\\t({t_word_end},{t_word_end+FADE_OUT_MS},"
                            f"\\1c&HFFFFFF&\\3c&H000000&\\bord{outline:.1f}\\blur0)"
                        )
                        if i == 0:
                            # Từ đầu tiên: không cần \r (state đã là Default = trắng)
                            block = "{" + in_anim + out_anim + "}"
                            parts.append(block + w['word'])
                        else:
                            # Từ thứ 2 trở đi: space + \r CANCEL các \t() carry-forward
                            # rồi schedule animation riêng cho từ này
                            block = " {\\r" + in_anim + out_anim + "}"
                            parts.append(block + w['word'])
                    text = "".join(parts)
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}\n")
                    continue

                # ============================================================
                # TEMPLATE STYLES (phân tích từ 5 video mẫu)
                # ============================================================
                # T1/T2/T4: cuốn 2–3 chữ/lần theo lời (mỗi cụm 1 Dialogue riêng)
                if style_mode in ('tpl_pop2', 'tpl_karaoke3', 'tpl_pophl'):
                    chunk_size = 2 if style_mode == 'tpl_pop2' else 3
                    seg_end = min(ev['end'] + 0.3, ev['scene_end'])
                    chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
                    for ci, chunk in enumerate(chunks):
                        c_start = chunk[0]['start']
                        c_end = chunks[ci + 1][0]['start'] if ci + 1 < len(chunks) else seg_end
                        c_end = max(c_end, c_start + 0.15)
                        start_ass = self.format_ass_time(c_start)
                        end_ass = self.format_ass_time(c_end)

                        if style_mode == 'tpl_pop2':
                            # Cả cụm 2 chữ: bật scale-pop + chữ vàng (punchy)
                            txt = ' '.join(w['word'] for w in chunk)
                            lead = ("{\\fscx72\\fscy72\\1c&H0000FFFF&"
                                    "\\t(0,140,\\fscx100\\fscy100)}")
                            f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{lead}{txt}\n")
                        else:
                            # 3 chữ: từ đang đọc vàng (+ phóng to nếu pophl), còn lại trắng
                            parts = []
                            for wi, w in enumerate(chunk):
                                t_ws = max(int((w['start'] - c_start) * 1000), 0)
                                t_we = max(int((w['end'] - c_start) * 1000), t_ws + 60)
                                reset = "\\r" if wi > 0 else ""
                                if style_mode == 'tpl_pophl':
                                    anim = (f"\\t({t_ws},{t_ws+60},\\1c&H0000FFFF&\\fscx114\\fscy114)"
                                            f"\\t({t_we},{t_we+110},\\1c&H00FFFFFF&\\fscx100\\fscy100)")
                                else:
                                    anim = (f"\\t({t_ws},{t_ws+60},\\1c&H0000FFFF&)"
                                            f"\\t({t_we},{t_we+110},\\1c&H00FFFFFF&)")
                                parts.append(f"{{{reset}{anim}}}{w['word']}")
                            f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{' '.join(parts)}\n")
                    continue

                # T3: cả câu hiện DẦN từng từ (fade-in tích luỹ), keyword vàng
                if style_mode == 'tpl_sentence':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    seg_start = ev['start']
                    kw = set(self.GLOW_KEYWORDS.get(lang_code, []))
                    parts = []
                    for w in words:
                        t_in = max(int((w['start'] - seg_start) * 1000), 0)
                        t_out = t_in + 260
                        clean = re.sub(r'[^\w]', '', w['word'], flags=re.UNICODE).lower()
                        col = "\\1c&H0000FFFF&" if clean in kw else ""
                        parts.append(
                            f"{{\\alpha&HFF&{col}\\t({t_in},{t_out},0.6,\\alpha&H00&)}}{w['word']}")
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{' '.join(parts)}\n")
                    continue

                # T5: cả câu hiện trắng 1 lần, keyword tô vàng cố định
                if style_mode == 'tpl_keyword':
                    start_ass = self.format_ass_time(ev['start'])
                    end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                    kw = set(self.GLOW_KEYWORDS.get(lang_code, []))
                    out_words = []
                    for w in words:
                        clean = re.sub(r'[^\w]', '', w['word'], flags=re.UNICODE).lower()
                        if clean in kw:
                            out_words.append(f"{{\\1c&H0000FFFF&}}{w['word']}{{\\r}}")
                        else:
                            out_words.append(w['word'])
                    f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{' '.join(out_words)}\n")
                    continue

                # Unknown mode → fallback classic
                start_ass = self.format_ass_time(ev['start'])
                end_ass = self.format_ass_time(min(ev['end'] + 0.3, ev['scene_end']))
                f.write(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{ev['text']}\n")

        # --- CĂN GIỮA KHUNG (cả dọc + ngang): chèn \an5\pos(tâm khung) vào MỌI
        #     dòng phụ đề, TRỪ dòng màn-đen (Effect=BS, phải giữ giữa MÀN HÌNH) ---
        if _box_center is not None:
            cx, cy = _box_center
            prefix = f"{{\\an5\\pos({cx},{cy})}}"
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                out = []
                for ln in lines:
                    if ln.startswith('Dialogue:'):
                        head, _, rest = ln.partition(':')
                        parts = rest.split(',', 9)   # 10 trường; parts[8]=Effect, parts[9]=Text
                        if len(parts) == 10:
                            if parts[8].strip() == 'BS':
                                parts[8] = ''        # màn đen → bỏ marker, KHÔNG căn khung
                            else:
                                parts[9] = prefix + parts[9]
                            ln = head + ':' + ','.join(parts)
                    out.append(ln)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.writelines(out)
            except Exception:
                pass

    def get_audio_duration(self, file_path):
        cmd = [get_ffmpeg('ffprobe'), '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
        try: return float(subprocess.check_output(cmd, stderr=subprocess.STDOUT, creationflags=0x08000000).decode().strip())
        except Exception: return 0

    # =========================================================
    # CACHE CHUẨN HÓA VIDEO NỀN (mode Tạo từ Video)
    # Clip nền (thường 4K) được transcode 1 LẦN về đúng kích thước
    # over-scale SW×SH @30fps. Các lần render sau dùng lại file cache
    # → bỏ hẳn bước decode 4K + scale trong filtergraph → render nhanh hơn nhiều.
    # =========================================================
    PROBE_CACHE_FILE = os.path.join(APP_DIR, "probe_cache.json")

    def _probe_cache_load(self):
        """Nạp cache probe từ đĩa (1 lần/phiên) → các lần render sau KHÔNG probe lại."""
        cache = getattr(self, '_bg_probe_cache', None)
        if cache is not None:
            return cache
        cache = {}
        try:
            import json
            with open(self.PROBE_CACHE_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            for k, v in raw.items():
                p, m, s = k.rsplit('|', 2)
                cache[(p, int(m), int(s))] = v
        except Exception:
            pass
        self._bg_probe_cache = cache
        return cache

    def _probe_cache_save(self):
        try:
            import json
            cache = getattr(self, '_bg_probe_cache', None) or {}
            raw = {f"{p}|{m}|{s}": v for (p, m, s), v in cache.items()}
            tmp = self.PROBE_CACHE_FILE + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(raw, f, ensure_ascii=False)
            os.replace(tmp, self.PROBE_CACHE_FILE)
        except Exception:
            pass

    def _probe_video_info(self, path):
        """Probe 1 file video → {'codec','w','h','fps','pix_fmt','dur'} hoặc None
        (không có luồng hình / hỏng). MỘT lần ffprobe lấy cả thông tin lẫn thời lượng.
        Cache theo (path, mtime, size) cả trong RAM lẫn trên đĩa."""
        try:
            st = os.stat(path)
            key = (path, st.st_mtime_ns, st.st_size)
        except Exception:
            return None
        cache = self._probe_cache_load()
        if key in cache:
            return cache[key]
        info = None
        try:
            import json
            r = subprocess.run(
                [get_ffmpeg('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=codec_name,width,height,avg_frame_rate,pix_fmt'
                                  ':format=duration',
                 '-of', 'json', path],
                capture_output=True, text=True, timeout=30, creationflags=0x08000000)
            if r.returncode == 0 and r.stdout:
                j = json.loads(r.stdout)
                st0 = (j.get('streams') or [{}])[0]
                w, h = int(st0.get('width') or 0), int(st0.get('height') or 0)
                if st0.get('codec_name') and w > 0 and h > 0:
                    info = {'codec': st0.get('codec_name'), 'w': w, 'h': h,
                            'fps': st0.get('avg_frame_rate', ''), 'pix_fmt': st0.get('pix_fmt', ''),
                            'dur': float((j.get('format') or {}).get('duration') or 0.0)}
        except Exception:
            info = None
        cache[key] = info
        return info

    def _probe_many(self, paths, label="clip", silent=False):
        """Probe NHIỀU file SONG SONG (8 luồng) — thư mục nền vài trăm clip probe
        tuần tự mất 10-20 phút; song song + cache đĩa → vài giây, lần sau ~0s.
        Trả về dict path→info (None = file hỏng)."""
        from concurrent.futures import ThreadPoolExecutor
        cache = self._probe_cache_load()
        need = []
        for p in paths:
            try:
                st = os.stat(p)
                if (p, st.st_mtime_ns, st.st_size) not in cache:
                    need.append(p)
            except Exception:
                need.append(p)
        if need and not silent:
            self.log(f"🔎 Đọc thông tin {len(need)}/{len(paths)} {label} (lần đầu, song song 8 luồng)...")
        if need:
            with ThreadPoolExecutor(max_workers=8) as ex:
                list(ex.map(self._probe_video_info, need))
            self._probe_cache_save()
        return {p: self._probe_video_info(p) for p in paths}

    def check_nvenc(self):
        """Kiểm tra GPU NVIDIA có hỗ trợ h264_nvenc không.
        KẾT QUẢ ĐƯỢC CACHE 1 LẦN/PHIÊN → không chạy lại test encode ở mỗi video."""
        cached = getattr(self, '_nvenc_ok_cache', None)
        if cached is not None:
            return cached
        result = self._check_nvenc_uncached()
        self._nvenc_ok_cache = result
        if result:
            # Dò 1 lần bộ tham số tăng tốc NVENC mà FFmpeg hiện tại hỗ trợ
            self._nvenc_extra_args = self._probe_nvenc_speed_args()
        else:
            self._nvenc_extra_args = []
        return result

    def _probe_nvenc_speed_args(self):
        """Thử preset tối ưu tốc độ/chất lượng cho h264_nvenc.
        FFmpeg mới: preset p1..p7 (p4 = cân bằng). FFmpeg cũ: preset fast.
        Trả về list args đã xác nhận chạy được (có thể rỗng)."""
        temp_test = os.path.join(TEMP_DIR, '_nvenc_preset_test.mp4')
        candidates = [
            ['-preset', 'p4', '-tune', 'hq', '-rc', 'vbr', '-cq', '23', '-b:v', '0'],
            ['-preset', 'fast'],
        ]
        for extra in candidates:
            try:
                t = subprocess.run(
                    [get_ffmpeg(), '-y', '-hide_banner',
                     '-f', 'lavfi', '-i', 'color=s=256x256:d=0.2:r=25',
                     '-c:v', 'h264_nvenc'] + extra + [temp_test],
                    capture_output=True, text=True, errors='replace', creationflags=0x08000000)
                ok = (t.returncode == 0 and os.path.exists(temp_test)
                      and os.path.getsize(temp_test) > 0)
                if os.path.exists(temp_test):
                    try: os.remove(temp_test)
                    except Exception: pass
                if ok:
                    self.log(f"⚡ NVENC dùng tham số tối ưu: {' '.join(extra)}")
                    return extra
            except Exception:
                pass
        return []

    def _calc_bg_workers(self):
        """Tự chọn số tiến trình FFmpeg song song cho pass ghép nền theo CẤU HÌNH MÁY:
        - CPU: mỗi worker ngốn ~2 luồng (decode + filter nhẹ) → tối đa cores/2.
        - GPU NVIDIA: driver giới hạn số phiên NVENC đồng thời
          (driver >= 550: 8 phiên, >= 530: 5, cũ hơn: 3). Chừa 1 phiên dự phòng
          cho pipeline chuẩn hóa nền chạy nền.
        Kết quả cache 1 lần/phiên làm việc."""
        cached = getattr(self, '_bg_workers_cache', None)
        if cached:
            return cached
        cpu = os.cpu_count() or 4
        w_cpu = max(2, cpu // 2)
        drv_txt = ""
        if self.check_nvenc():
            sess = 5  # mặc định an toàn cho driver đời mới không đọc được version
            try:
                r = subprocess.run(
                    ['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
                    capture_output=True, text=True, timeout=6, creationflags=0x08000000)
                if r.returncode == 0 and r.stdout.strip():
                    ver = int(float(r.stdout.strip().splitlines()[0].split('.')[0]))
                    drv_txt = f", driver NVIDIA v{ver}"
                    sess = 8 if ver >= 550 else (5 if ver >= 530 else 3)
            except Exception:
                pass
            if sess > 3:
                sess -= 1  # chừa 1 phiên NVENC cho việc khác
            workers = max(1, min(w_cpu, sess, 8))
        else:
            workers = max(1, min(w_cpu, 6))   # encode CPU: chỉ giới hạn theo core
        self._bg_workers_cache = workers
        self.log(f"🧠 Cấu hình máy: {cpu} luồng CPU{drv_txt} → "
                 f"dùng {workers} worker ghép nền song song.")
        return workers

    def _nvenc_encoder_args(self):
        """Args encoder NVENC + tham số tối ưu đã dò được."""
        return ['-c:v', 'h264_nvenc'] + list(getattr(self, '_nvenc_extra_args', []) or [])

    def _check_nvenc_uncached(self):
        try:
            r = subprocess.run([get_ffmpeg(), '-hide_banner', '-encoders'], capture_output=True, text=True, creationflags=0x08000000)
            if 'h264_nvenc' not in r.stdout:
                return False
            
            temp_test = os.path.join(TEMP_DIR, '_nvenc_test.mp4')
            
            # Thử nhiều cách encode khác nhau vì mỗi phiên bản FFmpeg khác nhau
            test_commands = [
                # Cách 1: Dùng rawvideo pipe (giống cách render thật)
                {
                    'cmd': [get_ffmpeg(), '-y', '-hide_banner',
                            '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-s', '256x256', '-r', '25',
                            '-i', '-', '-frames:v', '5', '-c:v', 'h264_nvenc', temp_test],
                    'input': bytes(256 * 256 * 3 // 2 * 5)  # 5 frames YUV420p
                },
                # Cách 2: Dùng lavfi không option thừa
                {
                    'cmd': [get_ffmpeg(), '-y', '-hide_banner',
                            '-f', 'lavfi', '-i', 'color=s=256x256:d=0.2:r=25',
                            '-c:v', 'h264_nvenc', temp_test],
                    'input': None
                },
                # Cách 3: Dùng lavfi với preset cũ (FFmpeg < 7.0)
                {
                    'cmd': [get_ffmpeg(), '-y', '-hide_banner',
                            '-f', 'lavfi', '-i', 'color=s=256x256:d=0.2:r=25',
                            '-c:v', 'h264_nvenc', '-preset', 'fast', temp_test],
                    'input': None
                },
            ]
            
            for i, test_cfg in enumerate(test_commands):
                try:
                    if test_cfg['input'] is not None:
                        test = subprocess.run(
                            test_cfg['cmd'], input=test_cfg['input'],
                            capture_output=True, text=False, creationflags=0x08000000
                        )
                    else:
                        test = subprocess.run(
                            test_cfg['cmd'],
                            capture_output=True, text=True, errors='replace', creationflags=0x08000000
                        )
                    
                    if os.path.exists(temp_test):
                        fsize = os.path.getsize(temp_test)
                        os.remove(temp_test)
                        if test.returncode == 0 and fsize > 0:
                            self.log(f"✅ NVENC test thành công (phương pháp {i+1})")
                            return True
                except Exception:
                    pass
            
            # Tất cả test đều fail - log lỗi từ lần thử cuối
            if os.path.exists(temp_test):
                try: os.remove(temp_test)
                except: pass
            return False
            
        except Exception as e:
            self.log(f"⚠️ Lỗi kiểm tra NVENC: {e}")
            return False

    def prepare_image(self, img_path, target_w, target_h):
        img = cv2.imread(img_path)
        if img is None: return np.zeros((target_h, target_w, 3), dtype=np.uint8)
        h, w = img.shape[:2]; aspect_img = w / h; aspect_target = target_w / target_h
        if aspect_img > aspect_target:
            new_w = int(h * aspect_target); start_x = (w - new_w) // 2
            img = img[:, start_x:start_x+new_w]
        else:
            new_h = int(w / aspect_target); start_y = (h - new_h) // 2
            img = img[start_y:start_y+new_h, :]
        # INTER_AREA nhanh hơn LANCZOS4 2-3 lần khi downscale, chất lượng tương đương
        interp = cv2.INTER_AREA if (img.shape[1] > target_w or img.shape[0] > target_h) else cv2.INTER_LINEAR
        return cv2.resize(img, (target_w, target_h), interpolation=interp)

    def get_animated_frame(self, img, elapsed_time, total_duration, effect_type, W, H):
        cx, cy = W / 2.0, H / 2.0; SPEED = 0.015; total_movement = SPEED * max(total_duration, 0.1)
        scale = 1.0; tx = 0.0; ty = 0.0
        
        if effect_type == 'zoom_in': scale = 1.0 + (SPEED * elapsed_time)
        elif effect_type == 'zoom_out': start_scale = 1.0 + total_movement; scale = start_scale - (SPEED * elapsed_time)
        elif effect_type == 'pan_left_to_right': scale = 1.0 + total_movement; max_tx = total_movement * cx; tx = (max_tx / 2.0) - (SPEED * elapsed_time * cx)
        elif effect_type == 'pan_right_to_left': scale = 1.0 + total_movement; max_tx = total_movement * cx; tx = -(max_tx / 2.0) + (SPEED * elapsed_time * cx)
        elif effect_type == 'pan_bottom_to_top': scale = 1.0 + total_movement; max_ty = total_movement * cy; ty = (max_ty / 2.0) - (SPEED * elapsed_time * cy)
        elif effect_type == 'pan_top_to_bottom': scale = 1.0 + total_movement; max_ty = total_movement * cy; ty = -(max_ty / 2.0) + (SPEED * elapsed_time * cy)
        elif effect_type == 'ken_burns': scale = 1.0 + (SPEED * elapsed_time); tx = -(SPEED * elapsed_time * cx / 2.0); ty = -(SPEED * elapsed_time * cy / 2.0)
            
        M = np.float32([[scale, 0, (1 - scale) * cx + tx], [0, scale, (1 - scale) * cy + ty]])
        return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR)

    # ============================================================
    # GIÓNG CHỮ ↔ TIẾNG bằng MMS forced-aligner (torchaudio / wav2vec2)
    # Model NHỎ chuyên "gióng văn bản có sẵn vào audio" — không phải model
    # nhận dạng giọng nói như Whisper → nhanh gấp ~10 lần, chạy theo cửa sổ
    # 30s nên nhẹ RAM/VRAM dù audio dài hàng giờ. Đa ngôn ngữ (Anh/Việt...).
    # ============================================================
    def _load_audio_16k(self, path):
        """Đọc audio → numpy float32 mono 16kHz qua ffmpeg đi kèm tool."""
        cmd = [get_ffmpeg(), '-nostdin', '-threads', '0', '-i', path,
               '-f', 'f32le', '-ac', '1', '-ar', '16000', '-']
        r = subprocess.run(cmd, capture_output=True, creationflags=0x08000000)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError("ffmpeg không đọc được audio")
        return np.frombuffer(r.stdout, dtype=np.float32).copy()

    @staticmethod
    def _mms_normalize_word(w):
        """Chuẩn hoá 1 từ về [a-z'] cho MMS: bỏ dấu tiếng Việt (đ→d), bỏ ký tự khác.
        (Chỉ dùng để GIÓNG — chữ hiển thị vẫn là chữ gốc có dấu.)"""
        import unicodedata
        s = w.replace('đ', 'd').replace('Đ', 'D')
        s = unicodedata.normalize('NFD', s)
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
        return re.sub(r"[^a-z']", '', s.lower())

    def _align_mms_words(self, voice_path, script_text, voice_dur, silent=False):
        """Gióng kịch bản vào audio → list {'word','start','end'} theo đúng thứ tự
        kịch bản. Trả None nếu không dùng được MMS → caller fallback Whisper."""
        try:
            import torch
            from torchaudio.pipelines import MMS_FA as bundle
        except Exception as e:
            self.log(f"ℹ Không có MMS aligner ({e}) → dùng Whisper.")
            return None
        try:
            tokens_orig = script_text.split()
            if not tokens_orig:
                return None
            norm = [self._mms_normalize_word(t) for t in tokens_orig]
            align_idx = [i for i, n in enumerate(norm) if n]   # từ có chữ để gióng
            if not align_idx:
                return None
            align_words = [norm[i] for i in align_idx]

            dev = 'cuda' if torch.cuda.is_available() else 'cpu'
            if getattr(self, '_mms_model', None) is None:
                # Trọng số lưu trong models\torch_hub (đi kèm gói mang đi → offline)
                _hub = os.path.join(APP_DIR, 'models', 'torch_hub')
                os.makedirs(_hub, exist_ok=True)
                torch.hub.set_dir(_hub)
                if not silent:
                    self.log("🧠 Nạp MMS forced-aligner (LẦN ĐẦU tải ~1.2GB, các lần sau offline)...")
                self._mms_model = bundle.get_model().to(dev).eval()
                self._mms_tokenizer = bundle.get_tokenizer()
                self._mms_aligner = bundle.get_aligner()
                self.log(f"🧠 MMS aligner sẵn sàng trên {'GPU' if dev == 'cuda' else 'CPU'}.")
            model, tokenizer, aligner = self._mms_model, self._mms_tokenizer, self._mms_aligner

            wav = self._load_audio_16k(voice_path)
            SR = 16000
            total = len(wav) / SR
            W = 30.0                      # cửa sổ audio (giây)
            n_words = len(align_words)
            out = [None] * n_words
            pos, t = 0, 0.0
            rate = 2.5                    # từ/giây (tự đo lại theo giọng đọc)
            n_fail = 0
            n_win = 0
            while pos < n_words and t < total - 0.05:
                if self.cancel_render:
                    return None
                t_end = min(t + W, total)
                seg = wav[int(t * SR): int(t_end * SR)]
                if len(seg) < SR * 0.5:
                    break
                k_try = int(max(8, min(160, rate * (t_end - t) * 0.6)))
                k_try = min(k_try, n_words - pos)
                ok = False
                last_end = t
                for _attempt in range(4):
                    # '*' (star) ở cuối = "phần audio còn lại là gì cũng được" →
                    # cửa sổ không cần kết thúc đúng chữ cuối.
                    words_win = align_words[pos:pos + k_try] + ['*']
                    try:
                        with torch.inference_mode():
                            x = torch.from_numpy(seg).unsqueeze(0).to(dev)
                            emission, _ = model(x)
                        spans = aligner(emission[0], tokenizer(words_win))[:k_try]
                        ratio = x.size(1) / emission.size(1)
                        for wi, sp in enumerate(spans):
                            out[pos + wi] = (sp[0].start * ratio / SR + t,
                                             sp[-1].end * ratio / SR + t)
                        last_end = out[pos + k_try - 1][1]
                        # Chữ cuối chạm sát mép cửa sổ → có thể chữ bị TRÀN → giảm K thử lại
                        if k_try > 8 and t_end < total and last_end > t_end - 0.4:
                            k_try = max(8, k_try // 2)
                            continue
                        ok = True
                        break
                    except Exception:
                        k_try = max(4, k_try // 2)
                        if k_try <= 4 and _attempt >= 2:
                            break
                if not ok:
                    # Hiếm: audio không khớp kịch bản đoạn này → dàn đều rồi đi tiếp
                    n_fail += 1
                    k_try = min(max(k_try, 4), n_words - pos)
                    span_len = (t_end - t) * 0.6
                    for wi in range(k_try):
                        out[pos + wi] = (t + span_len * wi / k_try, t + span_len * (wi + 1) / k_try)
                    last_end = t + span_len
                dur_used = max(0.3, last_end - t)
                rate = max(1.0, min(5.0, 0.5 * rate + 0.5 * (k_try / dur_used)))
                pos += k_try
                t = max(t + 0.5, last_end - 0.15)
                n_win += 1
                if not silent and n_win % 10 == 0:
                    self.after(0, self.set_render_stage, "whisper",
                               f"Gióng chữ vào tiếng... {min(100, int(100 * t / max(total, 1)))}%",
                               0.17 + 0.02 * min(1.0, t / max(total, 1)))
            if pos < n_words:   # hết audio mà còn chữ → dàn đều tới cuối
                s0 = out[pos - 1][1] if pos > 0 else t
                rem = n_words - pos
                span = max(0.5, total - s0)
                for wi in range(rem):
                    out[pos + wi] = (s0 + span * wi / rem, s0 + span * (wi + 1) / rem)

            # Ghép lại đủ mọi từ gốc; từ không có chữ (số/ký hiệu) gắn vào từ trước
            words, ai, last = [], 0, None
            for i, tok in enumerate(tokens_orig):
                if ai < len(align_idx) and align_idx[ai] == i:
                    s, e = out[ai]; ai += 1
                    last = (s, e)
                else:
                    s, e = last if last else (0.0, 0.0)
                words.append({'word': tok, 'start': float(s), 'end': float(max(e, s))})
            if n_fail and not silent:
                self.log(f"ℹ MMS: {n_fail} cửa sổ phải ước lượng (đoạn audio không khớp kịch bản).")
            return words
        except Exception as e:
            self.log(f"ℹ MMS aligner lỗi ({e}) → dùng Whisper.")
            return None

    def _events_from_words(self, words, voice_dur):
        """Gom từ (đã có mốc) thành câu phụ đề — mô phỏng logic cũ: ngắt ở . ? !
        | ngắt ở dấu phẩy khi có nghỉ >0.3s | ngắt khi lặng >0.5s | tối đa 45 ký tự."""
        events, cur = [], []

        def flush():
            if cur:
                events.append({'start': cur[0]['start'], 'end': cur[-1]['end'],
                               'text': ' '.join(w['word'] for w in cur),
                               'words': [dict(w) for w in cur]})
                cur.clear()

        for i, w in enumerate(words):
            if cur and len(' '.join(x['word'] for x in cur)) + 1 + len(w['word']) > 45:
                flush()
            cur.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else None
            gap = (nxt['start'] - w['end']) if nxt else 0.0
            tail = w['word'].rstrip('"\')]»')
            if tail.endswith(('.', '?', '!')) or gap > 0.5 or (tail.endswith(',') and gap > 0.3):
                flush()
        flush()
        out = []
        for i, ev in enumerate(events):
            next_start = events[i + 1]['start'] if i + 1 < len(events) else voice_dur
            out.append({'start': ev['start'], 'end': ev['end'], 'scene_end': next_start,
                        'text': ev['text'].strip(), 'gap_after': next_start - ev['end'],
                        'is_segment_ending': False, 'words': ev['words']})
        return out

    def _preprocess_task(self, task, silent=False):
        """Pre-process 1 task: gióng chữ↔tiếng (MMS, fallback Whisper) → subtitle → scene list."""
        import stable_whisper

        W, H = self.get_resolution(task['ratio_name'])
        lang_code = self._get_lang_code(task.get('language', 'English'))

        bgm_folder = task['bgm_folder']
        if not bgm_folder or not os.path.exists(bgm_folder):
            raise FileNotFoundError(
                f"Thư mục Nhạc nền không tồn tại: {bgm_folder}\n"
                f"Hãy chọn lại thư mục BGM hợp lệ."
            )
        valid_audio_exts = ('.mp3', '.wav', '.m4a')
        available_bgms = [os.path.join(bgm_folder, f) for f in os.listdir(bgm_folder) if f.lower().endswith(valid_audio_exts)]
        if not available_bgms:
            raise ValueError(f"Thư mục Nhạc nền '{bgm_folder}' không có file .mp3/.wav/.m4a!")
        chosen_bgm = random.choice(available_bgms)

        available_overlays = []
        overlay_folder = task['overlay_folder']
        if overlay_folder and os.path.exists(overlay_folder):
            valid_video_exts = ('.mp4', '.mov', '.avi')
            available_overlays = [os.path.join(overlay_folder, f) for f in os.listdir(overlay_folder) if f.lower().endswith(valid_video_exts)]
        # Overlay CỤ THỂ user chọn → dùng cố định cho toàn video (thay vì random)
        _ov_spec = task.get('overlay_file', '')
        if _ov_spec and os.path.isfile(_ov_spec):
            available_overlays = [_ov_spec]
            if not silent:
                self.log(f"✨ Overlay cố định: {os.path.basename(_ov_spec)}")

        # Validate voice_file tồn tại trước khi xử lý
        if not task.get('voice_file') or not os.path.exists(task['voice_file']):
            raise FileNotFoundError(
                f"File voice không tồn tại: {task.get('voice_file', '(rỗng)')}"
            )
        if not task.get('txt_file') or not os.path.exists(task['txt_file']):
            raise FileNotFoundError(
                f"File kịch bản (TXT) không tồn tại: {task.get('txt_file', '(rỗng)')}"
            )

        voice_dur = self.get_audio_duration(task['voice_file'])
        
        if not silent:
            self.after(0, self.set_render_stage, "whisper", "Đọc kịch bản và voice file...", 0.16)
        
        with open(task['txt_file'], 'r', encoding='utf-8') as f: script_content = f.read()
        script_content = re.sub(r'<#\d+(?:\.\d+)?#>', '', script_content)

        if self.cancel_render: return None

        # ===== ĐƯỜNG CHÍNH: MMS forced-aligner (nhanh, nhẹ) =====
        subtitle_events = None
        if not task.get('use_whisper_align'):
            if not silent:
                self.after(0, self.set_render_stage, "whisper", "Gióng chữ vào tiếng (MMS aligner)...", 0.17)
            _t0 = time.time()
            _wds = self._align_mms_words(task['voice_file'], script_content, voice_dur, silent=silent)
            if _wds:
                subtitle_events = self._events_from_words(_wds, voice_dur)
                self.log(f"⏱ MMS gióng xong: {time.time() - _t0:.1f}s cho audio "
                         f"{voice_dur / 60:.1f} phút ({len(_wds)} từ, {len(subtitle_events)} câu).")
        if self.cancel_render: return None

        # ===== DỰ PHÒNG: Whisper align (khi MMS không dùng được) =====
        if subtitle_events is None and (not hasattr(self, '_whisper_model') or self._whisper_model is None):
            if not silent:
                self.log("🧠 Đang tải AI model lần đầu (sẽ cache cho các video sau)...")
                self.after(0, self.set_render_stage, "whisper", "Tải Whisper AI model (lần đầu ~30s)...", 0.17)
            # Ưu tiên model đi kèm trong thư mục models\ (chạy OFFLINE trên máy mới,
            # không phải tải lại ~145MB); thiếu thì whisper tự tải về cache mặc định.
            # Dò thiết bị: GPU (cuda) → phân tích nhanh 5-10s; CPU → chậm hơn nhiều
            _dev = 'cpu'
            try:
                import torch
                self.log(f"🐍 Python: {sys.executable}")
                self.log(f"🔥 torch: {getattr(torch, '__version__', '?')}")
                if torch.cuda.is_available():
                    _dev = 'cuda'
            except Exception:
                pass
            _models_dir = os.path.join(APP_DIR, "models")
            if os.path.isfile(os.path.join(_models_dir, "base.pt")):
                self._whisper_model = stable_whisper.load_model('base', device=_dev, download_root=_models_dir)
            else:
                self._whisper_model = stable_whisper.load_model('base', device=_dev)
            if _dev == 'cuda':
                self.log("🧠 Whisper chạy trên GPU (CUDA) — phân tích nhanh.")
            else:
                self.log("⚠ Whisper đang chạy trên CPU → PHÂN TÍCH CHẬM.")
                self.log("   Nguyên nhân: PyTorch trên máy là bản CPU (thường do cài lại thư viện).")
                self.log("   Khắc phục: chạy file  CAI_TORCH_GPU.bat  (cần GPU NVIDIA).")
        if subtitle_events is None:
            model = self._whisper_model
            if not silent:
                self.after(0, self.set_render_stage, "whisper", "AI phân tích từng từ trong voice...", 0.18)
            if silent:
                self.log("⏳ Pipeline: Đang phân tích kịch bản video tiếp theo...")

            # fast_mode=True: stable-ts đời mới (>=2.17) mặc định căn chỉnh "kỹ" →
            # CHẬM. fast_mode bỏ các lượt tinh chỉnh. Bản cũ không có tham số → bỏ qua.
            _align_kw = dict(language=lang_code, verbose=False)

            def _do_align():
                try:
                    return model.align(task['voice_file'], script_content, fast_mode=True, **_align_kw)
                except TypeError:
                    return model.align(task['voice_file'], script_content, **_align_kw)

            # Chạy đường NHANH (SDPA) trước; chỉ khi stable-ts cũ dính lỗi
            # "'NoneType' object is not subscriptable" mới tắt SDPA chạy lại.
            _t0 = time.time()
            try:
                result = _do_align()
            except TypeError as _te:
                if 'subscriptable' not in str(_te):
                    raise
                try:
                    from whisper.model import disable_sdpa
                except ImportError:
                    raise _te
                self.log("ℹ stable-ts đời cũ + whisper mới → tắt SDPA, căn chỉnh lại...")
                with disable_sdpa():
                    result = _do_align()
            self.log(f"⏱ Whisper căn chỉnh xong: {time.time() - _t0:.1f}s cho audio {voice_dur/60:.1f} phút.")
            result.split_by_punctuation([('.', ' '), ('?', ' '), ('!', ' '), (',', ' ')])
            result.split_by_gap(0.5)
            result.merge_by_gap(0.3)
            result.split_by_length(max_chars=45)

            if self.cancel_render: return None

            segments = result.segments
            subtitle_events = []
            for i, seg in enumerate(segments):
                next_start = segments[i+1].start if i+1 < len(segments) else voice_dur
                gap = next_start - seg.end
                words = []
                if hasattr(seg, 'words') and seg.words:
                    for w in seg.words:
                        try:
                            words.append({'word': w.word.strip(),
                                          'start': float(w.start), 'end': float(w.end)})
                        except Exception:
                            pass
                subtitle_events.append({
                    'start': seg.start, 'end': seg.end, 'scene_end': next_start,
                    'text': seg.text.strip(), 'gap_after': gap, 'is_segment_ending': False,
                    'words': words
                })

        is_jesus = task.get('render_mode') == 'jesus_split'

        last_blackscreen_time = None
        for i in range(len(subtitle_events)):
            if subtitle_events[i]['gap_after'] >= 1.5:
                current_time = subtitle_events[i]['scene_end']
                if current_time < 120.0: continue
                if last_blackscreen_time is not None and (current_time - last_blackscreen_time) < 180.0: continue
                last_blackscreen_time = current_time
                accumulated_time = 0.0
                idx = i
                while idx >= 0:
                    subtitle_events[idx]['is_segment_ending'] = True
                    accumulated_time += (subtitle_events[idx]['scene_end'] - subtitle_events[idx]['start'])
                    if accumulated_time >= 5.0: break
                    idx -= 1

        # Tên file ASS riêng cho mỗi video (tránh ghi đè khi pipeline)
        ass_id = hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]
        temp_ass = os.path.join(TEMP_DIR, f"subtitle_{ass_id}.ass")
        style_name = task.get('subtitle_style', 'Cổ điển')
        style_mode = self.SUBTITLE_STYLE_MAP.get(style_name, 'classic')
        if not silent:
            self.log(f"📝 Kiểu phụ đề: {style_name}")
        # Cả 2 mode: nếu user đã kéo-thả ô phụ đề (enabled) → đặt theo ô đó,
        # ngược lại giữ canh giữa mặc định.
        kb_tb = task.get('kb_text_layout')
        if kb_tb and kb_tb.get('enabled'):
            ass_layout = 'right_box'
            ass_text_box = {'x': kb_tb.get('x', 0.1), 'y': kb_tb.get('y', 0.66),
                            'w': kb_tb.get('w', 0.8), 'h': kb_tb.get('h')}
        else:
            ass_layout = 'center'
            ass_text_box = None
        # Chuyển hoa/thường theo cài đặt user (AA / Aa / aa)
        _case = task.get('sub_case', 'keep')
        if _case != 'keep':
            for _ev in subtitle_events:
                _ev['text'] = self._apply_case_text(_ev['text'], _case)
                for _wd in _ev.get('words') or []:
                    _wd['word'] = self._apply_case_text(_wd['word'], _case)
        self.write_ass_subtitle(subtitle_events, task['font_val'], task['fontsize_val'], task['outline_val'], temp_ass, W, H, lang_code, style_mode, layout=ass_layout, text_box=ass_text_box)
        # Màu chữ/viền + glow user chọn (chỉ ghi đè khi khác mặc định để
        # giữ nguyên bảng màu riêng của các style Karaoke/Divine Glow...)
        self._apply_ass_overrides(
            temp_ass,
            text_color=(task.get('sub_text_color') or None)
                       if task.get('sub_text_color', '#FFFFFF') != '#FFFFFF' else None,
            outline_color=(task.get('sub_outline_color') or None)
                          if task.get('sub_outline_color', '#000000') != '#000000' else None,
            glow=int(task.get('sub_glow', 0)))

        # =========== NHÁNH MODE JESUS: chọn nền + ảnh Jesus rồi trả về sớm ===========
        if is_jesus:
            # --- CID: video nhạc nối cuối (giống mode ảnh) ---
            CID_EXTRA = 5.0  # 5s chuyển tiếp (nền+Jesus chạy thêm) trước khi nối CID
            cid_videos = []
            cid_total_dur = 0.0
            cid_folder = self.cid_folder
            if cid_folder and os.path.isdir(cid_folder):
                valid_video = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
                cid_files = [os.path.join(cid_folder, f) for f in os.listdir(cid_folder) if f.lower().endswith(valid_video)]
                if cid_files:
                    random.shuffle(cid_files)
                    _cinfo = self._probe_many(cid_files, label="video CID", silent=silent)
                    for cf in cid_files:
                        dur = float((_cinfo.get(cf) or {}).get('dur') or 0.0)
                        if dur > 0:
                            cid_videos.append({'path': cf, 'duration': dur})
                            cid_total_dur += dur
                    if cid_videos and not silent:
                        self.log(f"🎬 CID: {len(cid_videos)} video, tổng {cid_total_dur:.1f}s")
            # Nền phải phủ thêm 5s chuyển tiếp nếu có CID
            bg_target = voice_dur + (CID_EXTRA if cid_videos else 0.0)

            # --- Nguồn video nền: random từ thư mục ---
            valid_bg_exts = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
            bg_folder = task.get('bg_video_folder', '')
            if not bg_folder or not os.path.isdir(bg_folder):
                raise FileNotFoundError(f"Thư mục Video nền không tồn tại: {bg_folder}")
            bg_videos = [os.path.join(bg_folder, f) for f in os.listdir(bg_folder) if f.lower().endswith(valid_bg_exts)]
            if not bg_videos:
                raise ValueError(f"Thư mục Video nền '{bg_folder}' không có file video!")

            # Probe MỘT lần cho cả thư mục (song song + cache đĩa): vừa lọc file
            # hỏng/không có hình, vừa lấy luôn thời lượng → không còn probe tuần tự
            # 2 lần/clip (nguyên nhân "phân tích" đứng 10-20 phút với thư mục lớn).
            if not silent:
                self.after(0, self.set_render_stage, "whisper", "Đọc thư mục video nền...", 0.19)
            _binfo = self._probe_many(bg_videos, label="clip nền", silent=silent)
            _bad = [v for v in bg_videos if _binfo.get(v) is None]
            if _bad:
                for _b in _bad:
                    if not silent:
                        self.log(f"⚠ Bỏ qua clip nền lỗi/không có hình: {os.path.basename(_b)}")
                bg_videos = [v for v in bg_videos if v not in set(_bad)]
            if not bg_videos:
                raise ValueError(f"Thư mục Video nền '{bg_folder}' không có file video hợp lệ nào!")

            def _bg_dur(v):
                return float((_binfo.get(v) or {}).get('dur') or 0.0)

            BG_SAFETY = 1.0  # dư 1s để chắc chắn không hụt cuối video
            XF = self.jesus_xfade_dur  # mỗi xfade "ăn" T giây (dùng T tối đa để ước lượng → luôn phủ đủ)
            bg_clips = []
            bg_clip_durs = []
            effective_bg = 0.0  # thời lượng SAU khi trừ phần chồng lấp xfade
            last_pick = None
            guard = 0
            while effective_bg < bg_target + BG_SAFETY and guard < 4000:
                guard += 1
                shuffled = bg_videos[:]
                random.shuffle(shuffled)
                # tránh lặp ngay clip vừa dùng ở đầu vòng mới
                if last_pick and len(shuffled) > 1 and shuffled[0] == last_pick:
                    shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
                for v in shuffled:
                    d = _bg_dur(v)
                    if d <= 0:
                        continue
                    if not bg_clips:
                        effective_bg = d
                    else:
                        # clip kế tiếp chỉ thêm (d - T) thời lượng mới do chồng lấp xfade
                        effective_bg += max(d - XF, 0.1)
                    bg_clips.append(v)
                    bg_clip_durs.append(d)
                    last_pick = v
                    if effective_bg >= bg_target + BG_SAFETY:
                        break
            if not bg_clips:
                raise ValueError("Không đọc được thời lượng của video nền nào (file hỏng?).")

            # --- MÀN HÌNH ĐEN + wipe/blink (dựng bằng FFmpeg cho mode video) ---
            REVEAL_DUR = 1.0
            main_dur = voice_dur + (CID_EXTRA if cid_videos else 0.0)
            bs_windows = []
            _j = 0
            while _j < len(subtitle_events):
                if subtitle_events[_j].get('is_segment_ending'):
                    w_start = float(subtitle_events[_j]['start'])
                    _k = _j
                    while _k + 1 < len(subtitle_events) and subtitle_events[_k + 1].get('is_segment_ending'):
                        _k += 1
                    w_end = float(subtitle_events[_k]['scene_end'])
                    # chỉ nhận nếu đủ dài và nằm trong phần chính
                    if (w_end - w_start) >= 1.5 and w_end < voice_dur:
                        bs_windows.append({
                            'start': round(w_start, 3),
                            'end': round(w_end, 3),
                            'reveal': round(w_end - REVEAL_DUR, 3),
                            'effect': random.choice(['wipe', 'blink'])})
                    _j = _k + 1
                else:
                    _j += 1

            # SFX wipe/blink phát tại điểm bắt đầu "mở ra"
            sfx_wipe_ms = []
            sfx_blink_ms = []
            for w in bs_windows:
                ts = max(int(w['reveal'] * 1000), 0)
                (sfx_wipe_ms if w['effect'] == 'wipe' else sfx_blink_ms).append(ts)

            # Đoạn đổi particle overlay: cắt tại đầu mỗi màn đen → mỗi đoạn 1 overlay khác
            overlay_segments = []
            if available_overlays and self.jesus_overlay_opacity > 0:
                cut_pts = sorted(set(w['start'] for w in bs_windows if 0 < w['start'] < main_dur))
                seg_bounds = [0.0] + cut_pts + [main_dur]
                prev_ov = None
                for si in range(len(seg_bounds) - 1):
                    seg_dur = seg_bounds[si + 1] - seg_bounds[si]
                    if seg_dur <= 0.05:
                        continue
                    choices = [o for o in available_overlays if o != prev_ov] or available_overlays
                    ov = random.choice(choices)
                    prev_ov = ov
                    # đoạn cuối cộng dư 3s an toàn để track không bao giờ hụt
                    if si == len(seg_bounds) - 2:
                        seg_dur += 3.0
                    overlay_segments.append({'path': ov, 'dur': round(seg_dur, 3)})

            n_ov = len([l for l in (task.get('overlay_layers') or [])
                        if l.get('path') and os.path.exists(l['path'])])
            src_desc = f"{len(bg_clips)} clip random"
            if not silent:
                self.log(f"🎬 Nền: {src_desc} (~{effective_bg:.0f}s)  |  {n_ov} thành phần overlay"
                         f"  |  {len(bs_windows)} màn hình đen")
            else:
                self.log("✅ Pipeline: Đã chuẩn bị xong video nền tiếp theo!")

            return {
                'render_mode': 'jesus_split',
                'voice_dur': voice_dur, 'chosen_bgm': chosen_bgm,
                'available_overlays': available_overlays, 'temp_ass': temp_ass,
                'subtitle_events': subtitle_events,
                'bg_clips': bg_clips, 'bg_clip_durs': bg_clip_durs,
                'cid_videos': cid_videos, 'cid_total_dur': cid_total_dur,
                'bs_windows': bs_windows, 'overlay_segments': overlay_segments,
                'sfx_wipe_ms': sfx_wipe_ms, 'sfx_blink_ms': sfx_blink_ms,
                'W': W, 'H': H,
            }
        # =========== HẾT NHÁNH VIDEO NỀN ===========

        anim_pool = ['zoom_in', 'zoom_out', 'pan_left_to_right', 'pan_right_to_left',
                     'pan_bottom_to_top', 'pan_top_to_bottom', 'ken_burns']
        valid_exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
        available_images = [os.path.join(task['img_folder'], f) for f in os.listdir(task['img_folder'])
                            if f.lower().endswith(valid_exts)]
        sfx_wipe_timestamps_ms = []
        sfx_blink_timestamps_ms = []

        image_scenes = []
        current_scene_start = 0.0
        MIN_SCENE_TIME = 6.0; MAX_SCENE_TIME = 15.0
        i = 0
        while i < len(subtitle_events):
            ev = subtitle_events[i]
            if ev['is_segment_ending']:
                bs_start = current_scene_start
                bs_end = ev['scene_end']
                while i + 1 < len(subtitle_events) and subtitle_events[i+1]['is_segment_ending']:
                    i += 1; bs_end = subtitle_events[i]['scene_end']
                bs_effect = random.choice(['wipe', 'blink'])
                image_scenes.append({'start': bs_start, 'scene_end': bs_end, 'is_segment_ending': True, 'bs_effect': bs_effect})
                if bs_end < voice_dur:
                    ts = int((bs_end - 1.0) * 1000)
                    if bs_effect == 'wipe': sfx_wipe_timestamps_ms.append(ts)
                    else: sfx_blink_timestamps_ms.append(ts)
                if i + 1 < len(subtitle_events): current_scene_start = subtitle_events[i+1]['start']
            else:
                duration_so_far = ev['scene_end'] - current_scene_start
                is_last = (i == len(subtitle_events) - 1)
                has_punctuation = ev['text'].endswith(('.', '!', '?'))
                next_is_bs = (i + 1 < len(subtitle_events)) and subtitle_events[i+1]['is_segment_ending']
                if is_last or next_is_bs or (duration_so_far >= MIN_SCENE_TIME and has_punctuation) or (duration_so_far >= MAX_SCENE_TIME):
                    image_scenes.append({'start': current_scene_start, 'scene_end': ev['scene_end'], 'is_segment_ending': False})
                    if not is_last and not next_is_bs: current_scene_start = subtitle_events[i+1]['start']
            i += 1

        num_images_needed = len(image_scenes)
        final_images = []; last_image = None
        while len(final_images) < num_images_needed:
            temp_list = available_images.copy(); random.shuffle(temp_list)
            if temp_list[0] == last_image and len(temp_list) > 1: temp_list[0], temp_list[1] = temp_list[1], temp_list[0]
            final_images.extend(temp_list); last_image = final_images[-1]
        final_images = final_images[:num_images_needed]
        # Cảnh < 4s → ảnh đứng yên (không zoom/pan)
        image_effects = ['static' if (image_scenes[k]['scene_end'] - image_scenes[k]['start']) < 4.0
                         else random.choice(anim_pool) for k in range(num_images_needed)]

        # --- CID VIDEOS: Video nối cuối ---
        cid_videos = []
        cid_total_dur = 0
        cid_folder = self.cid_folder
        if cid_folder and os.path.exists(cid_folder):
            valid_video = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
            cid_files = [os.path.join(cid_folder, f) for f in os.listdir(cid_folder) if f.lower().endswith(valid_video)]
            if cid_files:
                random.shuffle(cid_files)
                _cinfo = self._probe_many(cid_files, label="video CID", silent=silent)
                for cf in cid_files:
                    dur = float((_cinfo.get(cf) or {}).get('dur') or 0.0)
                    if dur > 0:
                        cid_videos.append({'path': cf, 'duration': dur})
                        cid_total_dur += dur
                if cid_videos and not silent:
                    self.log(f"🎬 CID: {len(cid_videos)} video, tổng {cid_total_dur:.1f}s")

        # Thêm 5s ảnh chạy thêm sau voice để chuyển tiếp mượt sang CID
        CID_EXTRA_IMAGES = 5.0
        cid_extra_scenes = []
        cid_extra_images = []
        cid_extra_effects = []
        
        if cid_videos:
            extra_start = voice_dur
            extra_end = voice_dur + CID_EXTRA_IMAGES
            cid_extra_scenes.append({'start': extra_start, 'scene_end': extra_end, 'is_segment_ending': False})
            # Lấy 1 ảnh random cho 5s chuyển tiếp
            cid_extra_images = [random.choice(available_images)]
            cid_extra_effects = [random.choice(anim_pool)]

        if silent:
            self.log("✅ Pipeline: Đã phân tích xong video tiếp theo!")

        return {
            'voice_dur': voice_dur, 'chosen_bgm': chosen_bgm,
            'available_overlays': available_overlays, 'temp_ass': temp_ass,
            'subtitle_events': subtitle_events, 'image_scenes': image_scenes,
            'sfx_wipe_timestamps_ms': sfx_wipe_timestamps_ms,
            'sfx_blink_timestamps_ms': sfx_blink_timestamps_ms,
            'final_images': final_images, 'image_effects': image_effects,
            'W': W, 'H': H,
            'cid_videos': cid_videos, 'cid_total_dur': cid_total_dur,
            'cid_extra_scenes': cid_extra_scenes, 'cid_extra_images': cid_extra_images,
            'cid_extra_effects': cid_extra_effects
        }

    def _preprocess_next_in_background(self):
        """Chạy nền: pre-process video tiếp theo trong hàng đợi."""
        if self.cancel_render or not self.render_queue:
            return
        next_task = self.render_queue[0]  # Peek, không pop
        task_key = self._get_task_key(next_task)
        if task_key in self._preprocess_result:
            return  # Đã pre-process rồi
        try:
            result = self._preprocess_task(next_task, silent=True)
            if result and not self.cancel_render:
                self._preprocess_result[task_key] = result
        except Exception as e:
            self.log(f"⚠️ Pipeline pre-process lỗi (sẽ thử lại): {e}")

    def _get_task_key(self, task):
        """Sinh key unique cho task để cache pre-process result.
        Dùng id(task) đảm bảo unique cho từng task."""
        return id(task)

    # ============================================================
    # MODE "Jesus + Video nền" — render bằng filtergraph thuần FFmpeg
    # ============================================================
    def _detect_encoder_args(self):
        """Trả về list enc_args (h264_nvenc hoặc libx264) hoặc None nếu user hủy.
        Dùng chung logic GPU detection + CPU fallback dialog."""
        if self.check_nvenc():
            self.log("🚀 GPU NVIDIA detected → Dùng h264_nvenc để tăng tốc!")
            return self._nvenc_encoder_args()
        self.log("⚠️ Không tìm thấy GPU NVENC hoặc NVENC không hoạt động.")
        user_choice = threading.Event()
        user_accepted = [False]

        def ask_cpu_fallback():
            user_accepted[0] = messagebox.askyesno(
                "Không tìm thấy GPU",
                "⚠️ Không thể sử dụng GPU (NVENC) để render.\n\n"
                "Bạn có muốn tiếp tục render bằng CPU (chậm hơn) không?",
                icon='warning'
            )
            user_choice.set()

        self.after(0, ask_cpu_fallback)
        user_choice.wait()
        if not user_accepted[0]:
            return None
        self.log("⚙️ Tiếp tục render bằng CPU (libx264)...")
        return ['-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p']

    def _key_rembg(self, rgb_np):
        """Tách nền AI (rembg / U2-Net) — NHANH & rìa MỀM kiểu CapCut.
        Tối ưu tốc độ: chỉ suy luận trên bản THU NHỎ (≤768px) — U2-Net bên trong
        vốn resize về 320 nên chất lượng mask gần như y hệt mà nhanh + nhẹ hơn
        nhiều; xong upscale mask lên full-res + tinh rìa mềm. Trả alpha uint8/None."""
        try:
            from rembg import remove, new_session
        except Exception:
            if not getattr(self, '_rembg_warned', False):
                self.log("⚠️ Chưa cài 'rembg' (tách nền AI). Mở CMD chạy:  pip install rembg onnxruntime")
                self._rembg_warned = True
            return None
        try:
            import cv2, numpy as np
            from PIL import Image
            if getattr(self, '_rembg_session', None) is None:
                # Giới hạn số luồng onnxruntime → tách nền KHÔNG ăn hết CPU (đỡ lag máy)
                try:
                    import onnxruntime as _ort
                    _so = _ort.SessionOptions()
                    _so.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
                    _so.inter_op_num_threads = 1
                except Exception:
                    _so = None
                # u2net_human_seg: chuyên tách NGƯỜI (giữ áo/tóc tốt nhất). Fallback dần.
                for mdl in ("u2net_human_seg", "isnet-general-use", "u2net"):
                    try:
                        self._rembg_session = (new_session(mdl, sess_options=_so)
                                               if _so is not None else new_session(mdl))
                        self._rembg_model = mdl
                        self.log(f"🧠 rembg dùng model: {mdl}")
                        break
                    except Exception:
                        continue
            if getattr(self, '_rembg_session', None) is None:
                self.log("⚠️ Không tải được model rembg (kiểm tra mạng lần đầu).")
                return None

            h0, w0 = rgb_np.shape[:2]
            # Thu nhỏ về ≤768px cạnh dài để suy luận nhanh + nhẹ RAM
            WORK = 768
            scale = min(1.0, WORK / max(h0, w0))
            if scale < 1.0:
                small = cv2.resize(rgb_np.astype('uint8'),
                                   (max(1, int(w0 * scale)), max(1, int(h0 * scale))),
                                   interpolation=cv2.INTER_AREA)
            else:
                small = rgb_np.astype('uint8')

            fine = bool(getattr(self, 'jesus_key_fine', False))   # bật alpha matting?
            if fine:
                out = remove(Image.fromarray(small), session=self._rembg_session,
                             post_process_mask=True, alpha_matting=True,
                             alpha_matting_foreground_threshold=240,
                             alpha_matting_background_threshold=10,
                             alpha_matting_erode_size=3)
            else:
                out = remove(Image.fromarray(small), session=self._rembg_session,
                             post_process_mask=True)
            a_small = np.asarray(out.convert("RGBA"))[:, :, 3]

            # Upscale mask lên full-res (cubic = mượt) rồi tinh rìa mềm kiểu CapCut
            if a_small.shape[:2] != (h0, w0):
                alpha = cv2.resize(a_small, (w0, h0), interpolation=cv2.INTER_CUBIC)
            else:
                alpha = a_small
            # Rìa mềm NHẸ (chỉ 0.8px) — mượt răng cưa nhưng KHÔNG nhòe mất nét chủ thể
            if not fine:
                alpha = cv2.GaussianBlur(alpha, (0, 0), 0.8)
            return np.clip(alpha, 0, 255).astype(np.uint8)
        except Exception as e:
            self.log(f"⚠️ rembg lỗi ({e}).")
            return None

    def _key_grabcut(self, rgb_np):
        """Tách nền theo VÙNG (OpenCV GrabCut) — offline, không cần model.
        Giả định subject nằm giữa, gần kín khung. Trả về alpha uint8 hoặc None."""
        try:
            import cv2, numpy as np
            h, w = rgb_np.shape[:2]
            bgr = cv2.cvtColor(rgb_np.astype('uint8'), cv2.COLOR_RGB2BGR)
            mask = np.full((h, w), cv2.GC_PR_FGD, np.uint8)
            by, bx = int(h * 0.06), int(w * 0.06)
            mask[:by, :] = cv2.GC_BGD; mask[-by:, :] = cv2.GC_BGD
            mask[:, :bx] = cv2.GC_BGD; mask[:, -bx:] = cv2.GC_BGD
            bgd = np.zeros((1, 65), np.float64); fgd = np.zeros((1, 65), np.float64)
            cv2.grabCut(bgr, mask, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
            fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
            num, lbl, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
            if num > 1:
                big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                fg = np.where(lbl == big, 255, 0).astype(np.uint8)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
            frac = float((fg > 0).mean())
            if not (0.05 < frac < 0.99):
                return None
            return fg
        except Exception as e:
            self.log(f"⚠️ GrabCut lỗi ({e}).")
            return None

    def _key_color(self, rgb_np):
        """Tách nền theo MÀU (phông xanh / nền đặc / caro) — cho nền ĐƠN GIẢN, đồng nhất.
        Dò màu từ viền + chỉ giữ vùng nền nối với viền. Trả về alpha uint8 hoặc None."""
        try:
            import cv2, numpy as np
            rgb = rgb_np.astype(np.int16)
            h, w = rgb.shape[:2]
            R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
            mx = np.maximum(np.maximum(R, G), B); mn = np.minimum(np.minimum(R, G), B)
            band = max(2, min(h, w) // 50)
            border = np.concatenate([
                rgb[:band].reshape(-1, 3), rgb[-band:].reshape(-1, 3),
                rgb[:, :band].reshape(-1, 3), rgb[:, -band:].reshape(-1, 3)], axis=0)
            rmed, gmed, bmed = (int(np.median(border[:, i])) for i in range(3))
            neutral = border.max(1) - border.min(1); lightv = border.min(1)
            frac_nl = float(((neutral < 22) & (lightv > 170)).mean())
            is_green = gmed > 80 and gmed > rmed * 1.4 and gmed > bmed * 1.4
            is_blue = bmed > 80 and bmed > rmed * 1.4 and bmed > gmed * 1.2
            if is_green:
                bg = (G > R + 18) & (G > B + 18) & (G > 60)
            elif is_blue:
                bg = (B > R + 18) & (B > G + 10) & (B > 60)
            elif frac_nl > 0.5:
                bg = ((mx - mn) < 28) & (mn > 150)
            else:
                bg = (np.abs(R - rmed) + np.abs(G - gmed) + np.abs(B - bmed)) < 70
            bg = bg.astype(np.uint8)
            num, labels = cv2.connectedComponents(bg, connectivity=4)
            border_lbls = (set(labels[0, :].tolist()) | set(labels[-1, :].tolist()) |
                           set(labels[:, 0].tolist()) | set(labels[:, -1].tolist()))
            border_lbls.discard(0)
            if not border_lbls:
                return None
            final = np.isin(labels, list(border_lbls))
            fracbg = float(final.mean())
            if not (0.05 < fracbg < 0.985):
                return None
            return np.where(final, 0, 255).astype(np.uint8)
        except Exception as e:
            self.log(f"⚠️ Tách màu lỗi ({e}).")
            return None

    def _prepare_jesus_image(self, path, method=None, erode=None, feather=None):
        """Tách nền ảnh chân dung Jesus → PNG nền trong suốt (alpha thật).

        method:
          - 'auto'    : alpha sẵn → rembg(AI) → grabcut → màu  (thử lần lượt)
          - 'rembg'   : chỉ AI (tốt nhất cho nền ảnh thật phức tạp)
          - 'grabcut' : tách theo vùng, offline
          - 'color'   : tách theo màu (phông xanh / nền đặc / caro)
          - 'none'    : giữ nguyên ảnh
        erode   : co rìa N px (xoá lem nền / phông xanh còn sót)
        feather : làm mượt rìa (gauss sigma)
        Trả về (path_dùng, mô_tả)."""
        method = method or getattr(self, 'jesus_key_method', 'auto')
        erode = getattr(self, 'jesus_key_erode', 0) if erode is None else erode
        feather = getattr(self, 'jesus_key_feather', 1.0) if feather is None else feather
        try:
            import cv2, numpy as np
            from PIL import Image
            pil = Image.open(path)

            if method == 'none':
                return path, 'giữ nguyên (tắt tách nền)'

            # Ảnh đã có alpha sẵn (auto) → dùng luôn
            if method == 'auto' and (pil.mode in ('RGBA', 'LA') or
                                     (pil.mode == 'P' and 'transparency' in pil.info)):
                aa = np.asarray(pil.convert('RGBA'))[:, :, 3]
                if float((aa < 200).mean()) > 0.02:
                    return path, 'alpha có sẵn'

            rgb = np.asarray(pil.convert('RGB'))
            order = {'auto': ['rembg', 'grabcut', 'color'],
                     'rembg': ['rembg'], 'grabcut': ['grabcut'],
                     'color': ['color']}.get(method, ['rembg', 'grabcut', 'color'])

            alpha, used = None, ''
            for m in order:
                if m == 'rembg':
                    alpha = self._key_rembg(rgb); used = 'AI (rembg)'
                elif m == 'grabcut':
                    alpha = self._key_grabcut(rgb); used = 'GrabCut'
                elif m == 'color':
                    alpha = self._key_color(rgb); used = 'nền màu'
                if alpha is not None:
                    break
            if alpha is None:
                return path, f'{method}: không tách được nền → giữ nguyên'

            # Hậu xử lý: co rìa + mượt rìa (user chỉnh được)
            if erode and erode > 0:
                alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8), iterations=int(erode))
            if feather and feather > 0:
                alpha = cv2.GaussianBlur(alpha, (0, 0), float(feather))

            rgba = np.dstack([rgb, alpha]).astype(np.uint8)
            tag = hashlib.md5(f"{path}|{method}|{erode}|{feather}".encode()).hexdigest()[:8]
            out_path = os.path.join(TEMP_DIR, f"jesus_keyed_{tag}.png")
            Image.fromarray(rgba, 'RGBA').save(out_path)
            return out_path, f"{used} → đã tách nền"
        except Exception as e:
            self.log(f"⚠️ Lỗi tách nền Jesus ({e}) — dùng ảnh gốc.")
            return path, 'lỗi → giữ nguyên'

    def _render_jesus_split(self, task, pp):
        """Render 1 video mode 'jesus_split' (Tạo từ Video nền):
          - Nền: video stock (random nhiều clip xfade, hoặc 1 video cụ thể) + color grade + vignette
          - Particle overlay: screen blend (nếu có thư mục overlay)
          - Thành phần overlay (composer): logo / subscribe / chân dung / video phông xanh…
          - Phụ đề ASS (canh giữa hoặc theo ô kéo-thả)
          - Audio: voice + BGM
        Tất cả gói trong 1 lệnh FFmpeg (không cần vẽ frame Python)."""
        FPS = 30
        voice_dur = pp['voice_dur']
        chosen_bgm = pp['chosen_bgm']
        available_overlays = pp['available_overlays']
        temp_ass = pp['temp_ass']
        bg_clips = pp['bg_clips']
        bg_clip_durs = pp['bg_clip_durs']
        cid_videos = pp.get('cid_videos', [])
        cid_total_dur = pp.get('cid_total_dur', 0.0)
        W = pp['W']; H = pp['H']

        if self.cancel_render: return

        # --- Encoder (GPU/CPU) ---
        enc_args = self._detect_encoder_args()
        if enc_args is None:
            self.log("❌ Người dùng đã hủy render do không có GPU.")
            self.render_queue.clear()
            self.cancel_render = True
            self.after(0, self.update_progress_ui, 0.0, "Đã hủy - Không có GPU")
            self.after(0, lambda: self._set_render_status("❌ ĐÃ HỦY RENDER", "#8B0000"))
            self.after(0, lambda: self.btn_cancel_render.pack_forget())
            self.after(0, self.update_queue_ui)
            self.is_rendering = False
            return

        self.after(0, self.set_render_stage, "video", "Khởi động render video nền...", 0.20)

        # --- Output path ---
        if task.get('output_folder'):
            out_dir = task['output_folder']
        else:
            out_dir = os.path.dirname(task['voice_file'])
        custom_name = task.get('custom_name', '').strip()
        if custom_name:
            safe_name = self._safe_filename(custom_name)
            final_filename = f"{safe_name}.mp4"
        else:
            voice_basename = os.path.splitext(os.path.basename(task['voice_file']))[0]
            final_filename = f"FINAL_{voice_basename}.mp4"
        output_file = os.path.join(out_dir, final_filename)

        # --- CID: render phần chính ra file tạm rồi nối CID sau ---
        CID_EXTRA = 5.0  # nền + Jesus chạy thêm 5s trước khi nối CID (chuyển tiếp mượt)
        if cid_videos:
            main_video_dur = voice_dur + CID_EXTRA
            temp_main_video = os.path.join(TEMP_DIR, f"jmain_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.mp4")
            render_target = temp_main_video
            self.log(f"🎬 CID: {len(cid_videos)} video ({cid_total_dur:.0f}s) → chính {main_video_dur:.0f}s + nối CID sau")
        else:
            main_video_dur = voice_dur
            temp_main_video = None
            render_target = output_file

        voice_vol = task.get('voice_vol', 100) / 100.0
        bgm_vol = task.get('bgm_vol', 20) / 100.0
        ass_escaped = self.escape_path_for_ffmpeg(temp_ass)
        # Zoom + dịch khung theo cài đặt user (mặc định 112% giữa khung = punch-in cũ)
        SW, SH, bg_cx, bg_cy = self._bg_crop_expr(
            W, H, task.get('bg_zoom', 112), task.get('bg_off_x', 0), task.get('bg_off_y', 0))
        n_bg = len(bg_clips)

        # --- CHỐNG WinError 206 "command line too long" (giới hạn ~32k ký tự) ---
        # Voice dài → hàng trăm lượt clip nền, mỗi lượt 1 tham số -i.
        #  1) Path nằm trong thư mục tool (bg_cache/overlay/...) → dùng đường dẫn
        #     TƯƠNG ĐỐI, ffmpeg chạy với cwd=APP_DIR (rút ~70% độ dài).
        #  2) Vẫn quá dài → GỘP toàn bộ nền thành 1 file bằng concat demuxer
        #     (stream copy, gần như 0 giây vì clip đã chuẩn hóa cùng codec).
        def _short(p):
            try:
                rp = os.path.relpath(p, APP_DIR)
                if not rp.startswith('..'):
                    return rp
            except Exception:
                pass
            return p

        _bg_args_len = sum(len(_short(c)) + 6 for c in bg_clips)
        # Stream-copy chỉ AN TOÀN khi mọi clip cùng codec/độ phân giải/fps.
        # Kiểm tra THẬT bằng ffprobe (đã cache) thay vì suy đoán theo thư mục.
        def _bg_uniform():
            try:
                sig = None
                for _c in dict.fromkeys(bg_clips):   # unique — probe mỗi file 1 lần
                    _pi = self._probe_video_info(_c)
                    if _pi is None:
                        return False
                    _s = (_pi['codec'], _pi['w'], _pi['h'], _pi['fps'], _pi['pix_fmt'])
                    if sig is None:
                        sig = _s
                    elif _s != sig:
                        return False
                return True
            except Exception:
                return False
        # LƯU Ý: khi n_bg > 20, đường 2-PASS chia cụm phía dưới sẽ lo (xfade trong
        # cụm + xfade giữa các cụm) → KHÔNG gộp-copy ở đây nữa (gộp-copy sẽ giết
        # xfade). Chỉ giữ gộp-copy cho trường hợp hiếm: ít clip (≤20) mà đường dẫn
        # dài bất thường khiến lệnh vượt giới hạn.
        _all_cache = _bg_args_len > 24000 and 1 < n_bg <= 20 and _bg_uniform()
        if _bg_args_len > 24000 and 1 < n_bg <= 20 and not _all_cache:
            self.log("⚠ Lệnh dài nhưng các clip nền KHÔNG đồng nhất codec/kích thước/fps "
                     "→ không gộp copy được; sẽ render theo cụm (vẫn chạy bình thường).")
        if _all_cache:
            _tag = hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]
            _list_file = os.path.join(TEMP_DIR, f"bglist_{_tag}.txt")
            _merged = os.path.join(TEMP_DIR, f"bgmerged_{_tag}.mp4")
            try:
                with open(_list_file, 'w', encoding='utf-8') as _lf:
                    for _c in bg_clips:
                        _lf.write("file '" + _c.replace("'", "'\\''") + "'\n")
                self.log(f"⚠ {n_bg} lượt clip nền → lệnh quá dài. Gộp nền thành 1 file "
                         f"(concat copy, tắt xfade cho video này)...")
                _r = subprocess.run(
                    [get_ffmpeg(), '-y', '-f', 'concat', '-safe', '0', '-i', _list_file,
                     '-c', 'copy', _merged],
                    capture_output=True, creationflags=0x08000000)
                if _r.returncode == 0 and os.path.exists(_merged) and os.path.getsize(_merged) > 0:
                    bg_clips = [_merged]
                    bg_clip_durs = [sum(bg_clip_durs)]
                    n_bg = 1
                else:
                    self.log("⚠ Gộp nền thất bại — thử render trực tiếp (có thể vẫn lỗi 206).")
            except Exception:
                pass

        # --- TĂNG TỐC 2-PASS: GHÉP NỀN TRƯỚC bằng pass GPU riêng khi nhiều clip ---
        # Đo thực tế: chuỗi xfade hàng trăm node kìm tốc độ TOÀN BỘ graph chính.
        # Pass 1: chỉ ghép nền (normalize + xfade + zoom + màu) → NVENC ra file tạm.
        # Pass 2: graph chính chỉ còn 1 input nền → overlay/phụ đề/audio chạy nhanh.
        bg_premerged = False
        if n_bg > 20 and not self.cancel_render:
            _gb = max(-50, min(50, int(task.get('bg_brightness', 0)))) / 100.0
            _gc = max(50, min(150, int(task.get('bg_contrast', 103)))) / 100.0
            _gs = max(0, min(200, int(task.get('bg_saturation', 108)))) / 100.0
            _gfx = {'warm': ",colorbalance=rs=0.10:gs=0.02:bs=-0.10",
                    'cool': ",colorbalance=rs=-0.10:gs=0.00:bs=0.10",
                    'bw': ",hue=s=0",
                    'sepia': ",colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0",
                    'vignette': ",vignette=PI/5"}.get(task.get('bg_effect', 'none'), "")
            pm_tag = hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]
            pm_out = os.path.join(TEMP_DIR, f"bgpre_{pm_tag}.mp4")
            _grade = (f"eq=brightness={_gb:.3f}:contrast={_gc:.3f}:"
                      f"saturation={_gs:.3f}{_gfx}")

            # Chia nền thành CỤM ~24 clip, render SONG SONG nhiều tiến trình FFmpeg
            # (xfade trong cụm; mối nối giữa các cụm là cắt cứng ~4-5 phút/lần —
            # gần như không nhận ra). Xong nối các cụm bằng stream-copy (0 giây).
            CHUNK = 24
            _idx_chunks = [list(range(i, min(i + CHUNK, n_bg)))
                           for i in range(0, n_bg, CHUNK)]
            _workers = self._calc_bg_workers()

            # Render thẳng file gốc → scale bilinear: nhanh hơn bicubic ~20-30%,
            # khác biệt thị giác không đáng kể trên nền chuyển động
            _scale_flags = ":flags=bilinear"

            def _render_chunk(ci, idxs):
                """Render 1 cụm clip nền → file tạm. 2 nấc:
                  - Nấc 1: bình thường (xfade nội cụm).
                  - Nấc 2 (an toàn — cho file gốc lỗi timestamp/VFR): sửa PTS
                    (+genpts) + bỏ xfade trong cụm → gần như luôn qua được.
                Trả về path hoặc None."""
                nn = len(idxs)
                c_out = os.path.join(TEMP_DIR, f"bgpre_{pm_tag}_c{ci}.mp4")

                def _build(safe):
                    cparts = []
                    for j in range(nn):
                        cparts.append(
                            f"[{j}:v]scale={SW}:{SH}:force_original_aspect_ratio=increase{_scale_flags},"
                            f"crop={W}:{H}:{bg_cx}:{bg_cy},setsar=1,fps={FPS},format=yuv420p[bgn{j}]")
                    use_xfade = (self.jesus_xfade_dur > 0) and not safe
                    if nn == 1:
                        _src = "[bgn0]"
                    elif not use_xfade:
                        cparts.append("".join(f"[bgn{j}]" for j in range(nn)) +
                                      f"concat=n={nn}:v=1:a=0[bgcat]")
                        _src = "[bgcat]"
                    else:
                        _durs = [bg_clip_durs[gi] for gi in idxs]
                        _T = max(min(self.jesus_xfade_dur, min(_durs) * 0.5), 0.05)
                        _prev = "[bgn0]"; _acc = _durs[0]
                        for j in range(1, nn):
                            _off = max(_acc - _T, 0.0)
                            _o = "[bgcat]" if j == nn - 1 else f"[bgx{j}]"
                            cparts.append(f"{_prev}[bgn{j}]xfade=transition=fade:"
                                          f"duration={_T:.3f}:offset={_off:.3f}{_o}")
                            _acc += _durs[j] - _T; _prev = _o
                        _src = "[bgcat]"
                    cparts.append(f"{_src}{_grade}[bgout]")
                    c_script = os.path.join(TEMP_DIR, f"bgpre_{pm_tag}_c{ci}.txt")
                    with open(c_script, 'w', encoding='utf-8') as _ff:
                        _ff.write(";".join(cparts))
                    _enc = list(enc_args)
                    if 'h264_nvenc' in _enc:
                        # File trung gian: preset NHANH NHẤT (sẽ nén lại ở pass 2).
                        # KHÔNG -hwaccel cuda: 24 input × 7 cụm = ~170 phiên NVDEC → treo.
                        _enc += ['-preset', 'p1', '-cq', '19']
                    c_cmd = [get_ffmpeg(), '-y']
                    for gi in idxs:
                        if safe:
                            c_cmd += ['-fflags', '+genpts', '-err_detect', 'ignore_err']
                        c_cmd += ['-i', _short(bg_clips[gi])]
                    c_cmd += (['-filter_complex_script', c_script, '-map', '[bgout]']
                              + _enc + [c_out])
                    return c_cmd

                for _safe in (False, True):
                    if self.cancel_render:
                        return None
                    if _safe:
                        self.log(f"⚠ Cụm {ci + 1} lỗi → thử lại chế độ AN TOÀN "
                                 f"(sửa timestamp, bỏ xfade cụm này).")
                    _p = subprocess.Popen(_build(_safe), stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL,
                                          creationflags=0x08000000, cwd=APP_DIR)
                    self.active_processes.append(_p)
                    # Watchdog: cụm ~4 phút nội dung phải xong trong 15 phút — quá thì kill
                    try:
                        _p.wait(timeout=900)
                    except subprocess.TimeoutExpired:
                        try: _p.kill()
                        except Exception: pass
                        _p.wait()
                    if _p in self.active_processes:
                        self.active_processes.remove(_p)
                    if (not self.cancel_render and _p.returncode == 0
                            and os.path.exists(c_out) and os.path.getsize(c_out) > 0):
                        return c_out
                return None

            try:
                from concurrent.futures import ThreadPoolExecutor
                self.log(f"⚡ Pass 1/2: ghép {n_bg} clip nền — {len(_idx_chunks)} cụm × "
                         f"{_workers} tiến trình song song (GPU)...")
                self.after(0, self.set_render_stage, "video",
                           f"Ghép nền song song (0/{len(_idx_chunks)} cụm)", 0.21)
                _done = [0]
                _results = [None] * len(_idx_chunks)

                def _job(args):
                    ci, idxs = args
                    r = _render_chunk(ci, idxs)
                    _done[0] += 1
                    self.after(0, self.set_render_stage, "video",
                               f"Ghép nền song song ({_done[0]}/{len(_idx_chunks)} cụm)",
                               min(0.20 + (_done[0] / len(_idx_chunks)) * 0.20, 0.40))
                    return ci, r

                with ThreadPoolExecutor(max_workers=_workers) as _ex:
                    for ci, r in _ex.map(_job, list(enumerate(_idx_chunks))):
                        _results[ci] = r

                if not self.cancel_render and all(_results):
                    _T = self.jesus_xfade_dur
                    _merge_ok = False
                    if _T > 0 and len(_results) > 1:
                        # XFADE GIỮA CÁC CỤM: chỉ ~N cụm (thường <20) → lệnh NGẮN,
                        # chạy được dù tổng clip nền bao nhiêu. Nhờ đó xfade hiện ở
                        # MỌI mối nối (trong cụm ĐÃ có xfade + giữa cụm giờ cũng có).
                        try:
                            _cdurs = [self.get_audio_duration(r) for r in _results]
                            if all(d > 0 for d in _cdurs):
                                _in = []
                                for r in _results:
                                    _in += ['-i', r]
                                _parts = []
                                _prev = "[0:v]"; _acc = _cdurs[0]
                                _nn = len(_results)
                                for j in range(1, _nn):
                                    _t = max(min(_T, min(_cdurs[j - 1], _cdurs[j]) * 0.5), 0.05)
                                    _off = max(_acc - _t, 0.0)
                                    _o = "[xo]" if j == _nn - 1 else f"[xf{j}]"
                                    _parts.append(f"{_prev}[{j}:v]xfade=transition=fade:"
                                                  f"duration={_t:.3f}:offset={_off:.3f}{_o}")
                                    _acc += _cdurs[j] - _t; _prev = _o
                                _fc = os.path.join(TEMP_DIR, f"bgpre_{pm_tag}_xf.txt")
                                with open(_fc, 'w', encoding='utf-8') as _ff:
                                    _ff.write(";".join(_parts))
                                _enc2 = list(enc_args)
                                if 'h264_nvenc' in _enc2:
                                    _enc2 += ['-preset', 'p1', '-cq', '19']
                                _rc = subprocess.run(
                                    [get_ffmpeg(), '-y'] + _in +
                                    ['-filter_complex_script', _fc, '-map', '[xo]']
                                    + _enc2 + ['-t', f"{main_video_dur + 1.5:.3f}", pm_out],
                                    capture_output=True, creationflags=0x08000000)
                                _merge_ok = (_rc.returncode == 0 and os.path.exists(pm_out)
                                             and os.path.getsize(pm_out) > 0)
                        except Exception:
                            _merge_ok = False
                    if not _merge_ok:
                        # Fallback (xfade tắt / lỗi): nối copy nhanh, cắt cứng giữa cụm
                        _list = os.path.join(TEMP_DIR, f"bgpre_{pm_tag}_list.txt")
                        with open(_list, 'w', encoding='utf-8') as _lf:
                            for r in _results:
                                _lf.write("file '" + r.replace("'", "'\\''") + "'\n")
                        _rc = subprocess.run(
                            [get_ffmpeg(), '-y', '-f', 'concat', '-safe', '0', '-i', _list,
                             '-c', 'copy', '-t', f"{main_video_dur + 1.5:.3f}", pm_out],
                            capture_output=True, creationflags=0x08000000)
                        _merge_ok = (_rc.returncode == 0 and os.path.exists(pm_out)
                                     and os.path.getsize(pm_out) > 0)
                    if _merge_ok:
                        bg_clips = [pm_out]
                        bg_clip_durs = [main_video_dur + 1.5]
                        n_bg = 1
                        bg_premerged = True
                        self.log("⚡ Pass 2/2: overlay + phụ đề + âm thanh (graph nhẹ)...")
                    # Dọn file cụm
                    for r in _results:
                        try: os.remove(r)
                        except Exception: pass
                if not bg_premerged and not self.cancel_render:
                    self.log("⚠ Pass ghép nền song song thất bại → render 1 lệnh như cũ.")
            except Exception:
                pass
        if self.cancel_render:
            return

        # --- PASS 2 SONG SONG THEO THỜI GIAN (kỹ thuật render-farm) ---
        # Nền đã là 1 file → chia video thành K khúc thời gian, mỗi khúc 1 FFmpeg
        # chạy ĐỒNG THỜI (phụ đề/màn đen dịch mốc), audio mix 1 pass riêng,
        # cuối cùng concat copy + mux (0 giây). Thất bại → rơi về render 1 lệnh.
        if bg_premerged and not self.cancel_render:
            try:
                if self._render_jesus_parallel(task, pp, bg_clips[0], main_video_dur,
                                               enc_args, output_file, final_filename,
                                               out_dir, temp_main_video, W, H, FPS,
                                               chosen_bgm, voice_dur):
                    return
                if not self.cancel_render:
                    self.log("↩ Không chia khúc song song được → render 1 lệnh như cũ.")
            except Exception as _pe:
                self.log(f"↩ Song song theo thời gian lỗi ({_pe}) → render 1 lệnh như cũ.")
        if self.cancel_render:
            return

        # --- Inputs ---
        # Thứ tự: [0..n_bg-1] = clip nền (nối tiếp), particle overlay, voice, BGM, rồi layer composer
        cmd = [get_ffmpeg(), '-y']
        for clip in bg_clips:
            cmd += ['-i', _short(clip)]                           # clip nền (KHÔNG loop — nối tiếp)
        input_idx = n_bg
        # Track particle overlay: mỗi ĐOẠN (đổi sau mỗi màn đen) là 1 input riêng.
        # Không có màn đen → 1 đoạn duy nhất phủ cả video.
        overlay_segments = pp.get('overlay_segments', [])
        ov_seg_idx = []
        for seg in overlay_segments:
            cmd += ['-stream_loop', '-1', '-i', _short(seg['path'])]
            ov_seg_idx.append(input_idx); input_idx += 1
        voice_idx = input_idx; cmd += ['-i', _short(task['voice_file'])]; input_idx += 1
        bgm_idx = input_idx; cmd += ['-stream_loop', '-1', '-i', _short(chosen_bgm)]; input_idx += 1

        # --- Filtergraph ---
        parts = []
        # 1) Chuẩn hoá từng clip nền về cùng kích thước/fps/format
        #    (nền đã ghép ở pass 1 → chỉ cần passthrough, KHÔNG zoom/crop lại)
        for i in range(n_bg):
            if bg_premerged:
                parts.append(f"[{i}:v]format=yuv420p[bgn{i}]")
            else:
                parts.append(
                    f"[{i}:v]scale={SW}:{SH}:force_original_aspect_ratio=increase:flags=bilinear,"
                    f"crop={W}:{H}:{bg_cx}:{bg_cy},setsar=1,fps={FPS},format=yuv420p[bgn{i}]"
                )
        # 2) Ghép các clip nền bằng XFADE (chuyển cảnh mượt thay vì cắt cứng)
        if n_bg == 1:
            bg_src = "[bgn0]"
        elif self.jesus_xfade_dur <= 0:
            # Tắt xfade → nối cắt cứng (concat)
            parts.append("".join(f"[bgn{i}]" for i in range(n_bg)) + f"concat=n={n_bg}:v=1:a=0[bgcat]")
            bg_src = "[bgcat]"
        else:
            # T = thời lượng crossfade, kẹp < nửa clip ngắn nhất để offset luôn hợp lệ
            T = min(self.jesus_xfade_dur, min(bg_clip_durs) * 0.5)
            T = max(T, 0.05)
            prev = "[bgn0]"
            acc = bg_clip_durs[0]          # độ dài luỹ tiến của chuỗi đã ghép
            for i in range(1, n_bg):
                off = max(acc - T, 0.0)    # transition bắt đầu T giây trước khi chuỗi hiện tại kết thúc
                out = "[bgcat]" if i == n_bg - 1 else f"[bgx{i}]"
                parts.append(
                    f"{prev}[bgn{i}]xfade=transition=fade:duration={T:.3f}:offset={off:.3f}{out}"
                )
                acc += bg_clip_durs[i] - T
                prev = out
            bg_src = "[bgcat]"
        # 3) Color grade nhẹ cho dải nền (ĐÃ BỎ vignette — nó phủ 1 lớp tối mờ ở rìa
        #    khiến video nền bị ám tối, không sạch như mode ảnh).
        # Color grade + ánh sáng + effect theo cài đặt user (mặc định = grade cũ)
        _gb = max(-50, min(50, int(task.get('bg_brightness', 0)))) / 100.0
        _gc = max(50, min(150, int(task.get('bg_contrast', 103)))) / 100.0
        _gs = max(0, min(200, int(task.get('bg_saturation', 108)))) / 100.0
        _gfx = {'warm': ",colorbalance=rs=0.10:gs=0.02:bs=-0.10",
                'cool': ",colorbalance=rs=-0.10:gs=0.00:bs=0.10",
                'bw': ",hue=s=0",
                'sepia': ",colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0",
                'vignette': ",vignette=PI/5"}.get(task.get('bg_effect', 'none'), "")
        if bg_premerged:
            # Màu/effect đã áp ở pass 1 → không làm lại
            parts.append(f"{bg_src}null[bg]")
        else:
            parts.append(f"{bg_src}eq=brightness={_gb:.3f}:contrast={_gc:.3f}:"
                         f"saturation={_gs:.3f}{_gfx}[bg]")
        # 4) Particle overlay — TRACK có thể ĐỔI sau mỗi màn đen (nối các đoạn bằng concat).
        #    CỘNG THUẦN giống mode ảnh: nhân overlay *k rồi blend=addition (đen→cộng 0, sạch).
        if ov_seg_idx:
            k = float(self.jesus_overlay_opacity)
            seg_labels = []
            for si, (oidx, seg) in enumerate(zip(ov_seg_idx, overlay_segments)):
                parts.append(
                    f"[{oidx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},setsar=1,fps={FPS},trim=0:{seg['dur']:.3f},setpts=PTS-STARTPTS[oseg{si}]")
                seg_labels.append(f"[oseg{si}]")
            # TĂNG TỐC ~4x (đo thực tế): cộng sáng trên kênh LUMA (YUV) thay vì blend RGB.
            # Blend RGB cũ tốn 2 lần đổi hệ màu full-frame → chậm nhất toàn pipeline.
            # Particle giữ nguyên độ lấp lánh; lutyuv trừ 16 để nền đen của overlay
            # không tạo lớp xám mờ (mpeg range).
            _ov_lut = f"format=yuv420p,extractplanes=y,lutyuv=y='clip((val-16)*{k:.3f},0,255)'"
            if len(seg_labels) == 1:
                parts.append(f"{seg_labels[0]}{_ov_lut}[ovy]")
            else:
                parts.append("".join(seg_labels) +
                             f"concat=n={len(seg_labels)}:v=1:a=0,{_ov_lut}[ovy]")
            parts.append("[bg]format=yuv420p,extractplanes=y+u+v[bgy][bgu][bgv]")
            parts.append("[bgy][ovy]blend=all_mode=addition[mixy]")
            parts.append("[mixy][bgu][bgv]mergeplanes=0x001020:yuv420p[bgp]")
            base = "[bgp]"
        else:
            base = "[bg]"
        # 5) Thành phần overlay từ composer (KHÔNG burn ASS ở đây — để màn đen phủ TRƯỚC,
        #    phụ đề vẫn hiện TRÊN màn đen). Sóng radio đọc giọng qua input riêng.
        nwave = self._wave_layer_count(task)
        base_label = base.strip('[]')
        ov_inputs, ov_filter, input_idx = self._build_overlay_video_filter(
            task.get('overlay_layers'), input_idx, W, H, FPS, None,
            base_label=base_label, out_label='comp', voice_file=task.get('voice_file'))
        if ov_filter:
            cmd += ov_inputs
            parts.append(ov_filter.rstrip(';'))
            comp = "comp"
            n_lyr = len(task.get('overlay_layers') or [])
            self.log(f"🧩 Ghép {n_lyr} thành phần overlay vào video nền"
                     + (f" (gồm {nwave} sóng radio)." if nwave else "."))
        else:
            comp = base_label

        # 6) MÀN HÌNH ĐEN + hiệu ứng wipe/blink (dựng bằng overlay hộp đen trượt).
        bs_windows = pp.get('bs_windows', [])
        cur = comp
        if bs_windows:
            RD = 1.0  # thời lượng "mở ra"
            wipe_ws = [w for w in bs_windows if w['effect'] == 'wipe']
            blink_ws = [w for w in bs_windows if w['effect'] == 'blink']
            blk_full = []
            blk_half = []
            if wipe_ws:
                cmd += ['-f', 'lavfi', '-i', f'color=black:s={W}x{H}:r={FPS}']
                fidx = input_idx; input_idx += 1
                if len(wipe_ws) == 1:
                    blk_full = [f"{fidx}:v"]
                else:
                    labs = "".join(f"[bkf{i}]" for i in range(len(wipe_ws)))
                    parts.append(f"[{fidx}:v]split={len(wipe_ws)}{labs}")
                    blk_full = [f"bkf{i}" for i in range(len(wipe_ws))]
            if blink_ws:
                H2 = H // 2
                cmd += ['-f', 'lavfi', '-i', f'color=black:s={W}x{H2}:r={FPS}']
                hidx = input_idx; input_idx += 1
                ncopy = 2 * len(blink_ws)
                labs = "".join(f"[bkh{i}]" for i in range(ncopy))
                parts.append(f"[{hidx}:v]split={ncopy}{labs}")
                blk_half = [f"bkh{i}" for i in range(ncopy)]
            H2 = H // 2
            wi = 0; bi = 0
            for n, w in enumerate(bs_windows):
                s = w['start']; e = w['end']; r = w['reveal']
                if w['effect'] == 'wipe':
                    blab = blk_full[wi]; wi += 1
                    out = f"bsc{n}"
                    parts.append(
                        f"[{cur}][{blab}]overlay=x='if(lt(t,{r}),0,0-{W}*(t-{r})/{RD})':y=0:"
                        f"enable='between(t,{s},{e})':eof_action=pass[{out}]")
                    cur = out
                else:
                    top = blk_half[bi]; bi += 1
                    bot = blk_half[bi]; bi += 1
                    o1 = f"bsc{n}a"; o2 = f"bsc{n}b"
                    parts.append(
                        f"[{cur}][{top}]overlay=x=0:y='if(lt(t,{r}),0,0-{H2}*(t-{r})/{RD})':"
                        f"enable='between(t,{s},{e})':eof_action=pass[{o1}]")
                    parts.append(
                        f"[{o1}][{bot}]overlay=x=0:y='if(lt(t,{r}),{H2},{H2}+{H2}*(t-{r})/{RD})':"
                        f"enable='between(t,{s},{e})':eof_action=pass[{o2}]")
                    cur = o2
            self.log(f"⚫ {len(bs_windows)} màn hình đen (wipe/blink).")

        # 7) Phụ đề ASS (trên cùng — hiện cả trên màn đen) + fade in
        parts.append(f"[{cur}]ass='{ass_escaped}'[c4]")
        parts.append(f"[c4]fade=t=in:st=0:d=0.6,format=yuv420p[vout]")

        voice_src = f"[{voice_idx}:a]"

        # --- SFX wipe/blink (tại điểm "mở ra" của mỗi màn đen) ---
        _sfx_specs = []
        _wipe_file = task.get('sfx_wipe_file', '')
        _blink_file = task.get('sfx_blink_file', '')
        if pp.get('sfx_wipe_ms') and _wipe_file and os.path.exists(_wipe_file):
            for ts in pp['sfx_wipe_ms']:
                _sfx_specs.append((_wipe_file, ts))
        if pp.get('sfx_blink_ms') and _blink_file and os.path.exists(_blink_file):
            for ts in pp['sfx_blink_ms']:
                _sfx_specs.append((_blink_file, ts))
        sfx_labels = []
        for si, (sf, ts) in enumerate(_sfx_specs):
            cmd += ['-i', _short(sf)]
            sidx = input_idx; input_idx += 1
            parts.append(
                f"[{sidx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"adelay={ts}|{ts}[sfx{si}]")
            sfx_labels.append(f"[sfx{si}]")

        # Audio: voice + BGM (CID → pad voice + kéo dài BGM fade-out 5s chuyển tiếp)
        adur = "longest" if cid_videos else "first"
        if cid_videos:
            parts.append(
                f"{voice_src}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"volume={voice_vol:.2f},apad=whole_dur={main_video_dur:.4f}[voice]"
            )
            parts.append(
                f"[{bgm_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"volume={bgm_vol:.2f},atrim=0:{main_video_dur:.4f},"
                f"afade=t=out:st={max(main_video_dur-2, 0):.4f}:d=2[bgmn]"
            )
        else:
            parts.append(
                f"{voice_src}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"volume={voice_vol:.2f}[voice]"
            )
            parts.append(
                f"[{bgm_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                f"volume={bgm_vol:.2f}[bgmn]"
            )
        # Trộn voice+BGM như cũ; nếu có SFX → cộng thêm bằng amix normalize=0 (KHÔNG giảm voice)
        if sfx_labels:
            parts.append(f"[voice][bgmn]amix=inputs=2:duration={adur}:dropout_transition=2[vbmix]")
            parts.append(f"[vbmix]{''.join(sfx_labels)}amix=inputs={1+len(sfx_labels)}:duration={adur}:normalize=0[aout]")
        else:
            parts.append(f"[voice][bgmn]amix=inputs=2:duration={adur}:dropout_transition=2[aout]")
        filter_complex = ";".join(parts)

        # Ghi filtergraph ra FILE rồi dùng -filter_complex_script: tránh lỗi Windows
        # [WinError 206] "command line too long" khi nhiều clip nền → xfade rất dài.
        fc_script = os.path.join(TEMP_DIR, f"jesus_fc_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.txt")
        try:
            with open(fc_script, 'w', encoding='utf-8') as ff:
                ff.write(filter_complex)
        except Exception:
            fc_script = None

        if fc_script:
            cmd += ['-filter_complex_script', fc_script]
        else:
            cmd += ['-filter_complex', filter_complex]
        cmd += ['-map', '[vout]', '-map', '[aout]'] + enc_args + \
               ['-c:a', 'aac', '-t', f"{main_video_dur:.4f}", render_target]

        # Debug command ra file (giống nhánh CID) để dễ gửi dev khi lỗi
        debug_cmd = os.path.join(out_dir, f"DEBUG_JESUS_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.txt")
        try:
            with open(debug_cmd, 'w', encoding='utf-8') as df:
                df.write("JESUS RENDER COMMAND:\n")
                df.write(' '.join(f'"{a}"' if ' ' in a else a for a in cmd) + '\n\n')
                df.write("FILTER:\n" + filter_complex.replace(';', ';\n') + '\n')
        except Exception:
            pass

        self.log(f"🎬 Render video nền: {n_bg} clip nền "
                 f"(lệnh ~{sum(len(str(a)) + 1 for a in cmd)} ký tự).")

        # --- Chạy FFmpeg + theo dõi tiến độ qua stderr time= ---
        # cwd=APP_DIR để các đường dẫn tương đối (_short) hoạt động
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, errors='replace', creationflags=0x08000000,
            cwd=APP_DIR
        )
        self.active_processes.append(proc)

        err_lines = []
        for line in proc.stderr:
            if self.cancel_render:
                try: proc.kill()
                except Exception: pass
                break
            m = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
            if m and main_video_dur > 0:
                h_t, m_t, s_t = m.groups()
                sec = int(h_t) * 3600 + int(m_t) * 60 + float(s_t)
                _cap = 0.90 if cid_videos else 1.0   # chừa 0.90→1.0 cho bước nối CID
                pct = min(0.20 + (sec / main_video_dur) * (_cap - 0.20), _cap)
                self.after(0, self.set_render_stage, "video",
                           f"Render video nền 2/2 ({sec:.0f}/{main_video_dur:.0f}s)", pct)
            stripped = line.strip()
            if stripped and not any(stripped.startswith(p) for p in (
                    'frame=', 'Input #', 'Output #', 'Stream #', 'Metadata:',
                    'Duration:', 'Press', 'Stream mapping', 'configuration:',
                    'built with', 'ffmpeg version', 'lib')):
                err_lines.append(stripped)
                if len(err_lines) > 300: err_lines.pop(0)

        proc.wait()
        if proc in self.active_processes: self.active_processes.remove(proc)

        if self.cancel_render:
            return

        if proc.returncode == 0:
            try: os.remove(debug_cmd)
            except Exception: pass
            if cid_videos and temp_main_video and os.path.exists(temp_main_video):
                # Nối CID vào cuối
                self._concat_cid_jesus(task, temp_main_video, cid_videos, main_video_dur,
                                       cid_total_dur, output_file, final_filename,
                                       enc_args, out_dir, W, H, FPS)
            else:
                self.log(f"✅ HOÀN THÀNH (video nền): {final_filename}")
                self.after(0, lambda: self._set_render_status("✅ HOÀN THÀNH", "#1e5228"))
                self.after(0, self.update_progress_ui, 1.0, "Hoàn thành video (mode video nền)!")
        else:
            self.log(f"❌ LỖI KHI RENDER JESUS (code {proc.returncode})")
            relevant = [l for l in err_lines if any(k in l.lower() for k in
                        ['error', 'failed', 'invalid', 'no such', 'cannot', 'unable to', 'not found', 'unrecognized'])]
            if not relevant: relevant = err_lines[-8:]
            for e in relevant[-8:]:
                self.log(f"   ⚠ {e}")
            self.log(f"📄 Debug: {debug_cmd}")
            if DEBUG_LOGGER_AVAILABLE:
                try:
                    debug_logger.save_render_failure(
                        operation=f"render_jesus_{final_filename}",
                        command=cmd,
                        stderr_text="\n".join(err_lines),
                        extra_context={
                            "render_mode": "jesus_split",
                            "bg_clips_count": n_bg,
                            "bg_clips": bg_clips,
                            "overlay_layers": len(task.get('overlay_layers') or []),
                            "voice_dur": voice_dur,
                            "output_file": output_file,
                            "ffmpeg_returncode": proc.returncode,
                        }
                    )
                except Exception:
                    pass

    # ================================================================
    # PASS 2 SONG SONG THEO THỜI GIAN — chia video thành K khúc,
    # render đồng thời K tiến trình FFmpeg rồi nối lại (render-farm style)
    # ================================================================
    def _render_jesus_parallel(self, task, pp, bg_file, main_video_dur, enc_args,
                               output_file, final_filename, out_dir,
                               temp_main_video, W, H, FPS, chosen_bgm, voice_dur):
        """Trả về True nếu render xong hoàn chỉnh (kể cả CID + báo trạng thái).
        Trả về False → caller rơi về đường render 1 lệnh cũ."""
        # Sóng radio cần đọc voice theo frame → chưa hỗ trợ chia khúc
        if self._wave_layer_count(task) > 0:
            self.log("ℹ Có layer sóng radio → dùng render 1 lệnh (chưa hỗ trợ chia khúc).")
            return False
        workers = self._calc_bg_workers()
        K = int(min(workers, max(1, main_video_dur // 150)))
        if K < 2:
            return False

        cid_videos = pp.get('cid_videos') or []
        cid_total_dur = pp.get('cid_total_dur', 0.0)
        render_target = temp_main_video if cid_videos else output_file
        bs_windows = pp.get('bs_windows', [])
        overlay_segments = pp.get('overlay_segments', [])
        subtitle_events = pp.get('subtitle_events', [])
        tag = hashlib.md5((task['voice_file'] + 'par').encode()).hexdigest()[:8]

        # --- 1) CHỌN RANH GIỚI KHÚC: ưu tiên đặt tại ĐẦU MÀN HÌNH ĐEN
        #        (overlay đổi track ở đó sẵn → mối nối tàng hình) ---
        bs_starts = sorted(w['start'] for w in bs_windows if 30 < w['start'] < main_video_dur - 30)
        bounds = [0.0]
        seg_len = main_video_dur / K
        for i in range(1, K):
            ideal = seg_len * i
            pick = ideal
            if bs_starts:
                near = min(bs_starts, key=lambda s: abs(s - ideal))
                if abs(near - ideal) <= seg_len * 0.45:
                    pick = near
            if pick - bounds[-1] >= 45:
                bounds.append(round(pick, 3))
        bounds.append(round(main_video_dur, 3))
        bounds = sorted(set(bounds))
        if len(bounds) < 3:
            return False
        n_seg = len(bounds) - 1

        # Mốc tuyệt đối của từng đoạn overlay (để cắt theo khúc)
        ov_abs = []
        _acc = 0.0
        for seg in overlay_segments:
            ov_abs.append((_acc, _acc + seg['dur'], seg['path']))
            _acc += seg['dur']

        style_name = task.get('subtitle_style', 'Cổ điển')
        style_mode = self.SUBTITLE_STYLE_MAP.get(style_name, 'classic')
        lang_code = self._get_lang_code(task.get('language', 'English'))
        kb_tb = task.get('kb_text_layout')
        if kb_tb and kb_tb.get('enabled'):
            ass_layout, ass_box = 'right_box', {'x': kb_tb.get('x', 0.1),
                                                'y': kb_tb.get('y', 0.66),
                                                'w': kb_tb.get('w', 0.8), 'h': kb_tb.get('h')}
        else:
            ass_layout, ass_box = 'center', None

        self.log(f"⚡ Pass 2/2 SONG SONG: {n_seg} khúc × {workers} tiến trình "
                 f"(ranh giới đặt tại màn hình đen).")
        prog = [0.0] * n_seg
        prog_lock = threading.Lock()
        _last_ui = [0.0]

        def _update_ui():
            now = time.time()
            if now - _last_ui[0] < 0.6:
                return
            _last_ui[0] = now
            done = sum(prog)
            pct = min(0.42 + (done / max(main_video_dur, 1)) * 0.50, 0.92)
            self.after(0, self.set_render_stage, "video",
                       f"Render song song ({done:.0f}/{main_video_dur:.0f}s · {n_seg} khúc)", pct)

        def _seg_ass(si, t0, t1):
            """Sinh file ASS riêng cho khúc: sự kiện giao [t0,t1), dịch mốc -t0."""
            import copy as _copy
            evs = []
            for ev in subtitle_events:
                if ev['scene_end'] <= t0 or ev['start'] >= t1:
                    continue
                e = _copy.deepcopy(ev)
                e['start'] = max(0.0, e['start'] - t0)
                e['end'] = max(0.0, min(e['end'] - t0, t1 - t0))
                e['scene_end'] = max(e['end'], min(e['scene_end'] - t0, t1 - t0))
                for wd in e.get('words') or []:
                    wd['start'] = max(0.0, wd['start'] - t0)
                    wd['end'] = max(wd['start'], wd['end'] - t0)
                evs.append(e)
            path = os.path.join(TEMP_DIR, f"seg_{tag}_{si}.ass")
            self.write_ass_subtitle(evs, task['font_val'], task['fontsize_val'],
                                    task['outline_val'], path, W, H, lang_code,
                                    style_mode, layout=ass_layout, text_box=ass_box)
            self._apply_ass_overrides(
                path,
                text_color=(task.get('sub_text_color') or None)
                           if task.get('sub_text_color', '#FFFFFF') != '#FFFFFF' else None,
                outline_color=(task.get('sub_outline_color') or None)
                              if task.get('sub_outline_color', '#000000') != '#000000' else None,
                glow=int(task.get('sub_glow', 0)))
            return path

        def _build_seg_cmd(si, t0, t1, use_hw):
            dur = t1 - t0
            seg_out = os.path.join(TEMP_DIR, f"seg_{tag}_{si}.mp4")
            cmd = [get_ffmpeg(), '-y']
            if use_hw:
                cmd += ['-hwaccel', 'cuda']
            cmd += ['-ss', f'{t0:.3f}', '-t', f'{dur + 0.08:.3f}', '-i', bg_file]
            idx = 1
            # Overlay particle giao với khúc này
            pieces = []
            for (s_a, e_a, p_) in ov_abs:
                o_s, o_e = max(s_a, t0), min(e_a, t1)
                if o_e - o_s > 0.05:
                    pieces.append((o_e - o_s, p_))
            # QUAN TRỌNG: đoạn CUỐI phải dư 3s — track overlay hụt vài frame so với nền
            # là filter blend chờ vô hạn → khúc treo ở đoạn kết (giống render 1 lệnh gốc)
            if pieces:
                _ld, _lp = pieces[-1]
                pieces[-1] = (_ld + 3.0, _lp)
            p_idx = []
            for (_d, p_) in pieces:
                cmd += ['-stream_loop', '-1', '-i', p_]
                p_idx.append(idx); idx += 1
            parts = [f"[0:v]format=yuv420p[bg]"]
            if p_idx and self.jesus_overlay_opacity > 0:
                k = float(self.jesus_overlay_opacity)
                labs = []
                for j, (pi, (d_, _p)) in enumerate(zip(p_idx, pieces)):
                    parts.append(f"[{pi}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                                 f"crop={W}:{H},setsar=1,fps={FPS},trim=0:{d_:.3f},"
                                 f"setpts=PTS-STARTPTS[os{j}]")
                    labs.append(f"[os{j}]")
                _lut = f"format=yuv420p,extractplanes=y,lutyuv=y='clip((val-16)*{k:.3f},0,255)'"
                if len(labs) == 1:
                    parts.append(f"{labs[0]}{_lut}[ovy]")
                else:
                    parts.append("".join(labs) + f"concat=n={len(labs)}:v=1:a=0,{_lut}[ovy]")
                parts.append("[bg]format=yuv420p,extractplanes=y+u+v[by][bu][bv]")
                parts.append("[by][ovy]blend=all_mode=addition[my]")
                parts.append("[my][bu][bv]mergeplanes=0x001020:yuv420p[bgp]")
                base = "bgp"
            else:
                base = "bg"
            # Thành phần composer (logo/greenscreen — KHÔNG sóng radio)
            ov_inputs, ov_filter, idx = self._build_overlay_video_filter(
                task.get('overlay_layers'), idx, W, H, FPS, None,
                base_label=base, out_label=f'comp', voice_file=None)
            if ov_filter:
                cmd += ov_inputs
                parts.append(ov_filter.rstrip(';'))
                cur = "comp"
            else:
                cur = base
            # Màn hình đen giao khúc này (dịch mốc -t0)
            wins = []
            for w_ in bs_windows:
                if w_['end'] <= t0 or w_['start'] >= t1:
                    continue
                wins.append({'start': max(0.0, w_['start'] - t0),
                             'end': min(w_['end'] - t0, dur),
                             'reveal': min(max(w_['reveal'] - t0, 0.0), dur),
                             'effect': w_['effect']})
            if wins:
                RD = 1.0
                wipe_n = sum(1 for w_ in wins if w_['effect'] == 'wipe')
                blink_n = len(wins) - wipe_n
                blk_full, blk_half = [], []
                if wipe_n:
                    cmd += ['-f', 'lavfi', '-i', f'color=black:s={W}x{H}:r={FPS}']
                    fi = idx; idx += 1
                    if wipe_n == 1:
                        blk_full = [f"{fi}:v"]
                    else:
                        labs2 = "".join(f"[bkf{i2}]" for i2 in range(wipe_n))
                        parts.append(f"[{fi}:v]split={wipe_n}{labs2}")
                        blk_full = [f"bkf{i2}" for i2 in range(wipe_n)]
                if blink_n:
                    H2 = H // 2
                    cmd += ['-f', 'lavfi', '-i', f'color=black:s={W}x{H2}:r={FPS}']
                    hi = idx; idx += 1
                    labs2 = "".join(f"[bkh{i2}]" for i2 in range(2 * blink_n))
                    parts.append(f"[{hi}:v]split={2 * blink_n}{labs2}")
                    blk_half = [f"bkh{i2}" for i2 in range(2 * blink_n)]
                H2 = H // 2
                wi = bi = 0
                for n_, w_ in enumerate(wins):
                    s_, e_, r_ = w_['start'], w_['end'], w_['reveal']
                    if w_['effect'] == 'wipe':
                        bl = blk_full[wi]; wi += 1
                        o_ = f"bsc{n_}"
                        parts.append(f"[{cur}][{bl}]overlay=x='if(lt(t,{r_}),0,0-{W}*(t-{r_})/{RD})':y=0:"
                                     f"enable='between(t,{s_},{e_})':eof_action=pass[{o_}]")
                        cur = o_
                    else:
                        tp = blk_half[bi]; bi += 1
                        bt = blk_half[bi]; bi += 1
                        o1, o2 = f"bsc{n_}a", f"bsc{n_}b"
                        parts.append(f"[{cur}][{tp}]overlay=x=0:y='if(lt(t,{r_}),0,0-{H2}*(t-{r_})/{RD})':"
                                     f"enable='between(t,{s_},{e_})':eof_action=pass[{o1}]")
                        parts.append(f"[{o1}][{bt}]overlay=x=0:y='if(lt(t,{r_}),{H2},{H2}+{H2}*(t-{r_})/{RD})':"
                                     f"enable='between(t,{s_},{e_})':eof_action=pass[{o2}]")
                        cur = o2
            # Phụ đề khúc + fade in (chỉ khúc đầu)
            ass_p = _seg_ass(si, t0, t1)
            parts.append(f"[{cur}]ass='{self.escape_path_for_ffmpeg(ass_p)}'[c4]")
            if si == 0:
                parts.append(f"[c4]fade=t=in:st=0:d=0.6,format=yuv420p[vout]")
            else:
                parts.append(f"[c4]format=yuv420p[vout]")
            fc = os.path.join(TEMP_DIR, f"seg_{tag}_{si}_fc.txt")
            with open(fc, 'w', encoding='utf-8') as f_:
                f_.write(";".join(parts))
            cmd += ['-filter_complex_script', fc, '-map', '[vout]', '-an'] \
                   + list(enc_args) + ['-t', f'{dur:.3f}', seg_out]
            return cmd, seg_out

        def _run_seg(si):
            t0, t1 = bounds[si], bounds[si + 1]
            err_tail = []
            for use_hw in ((True, False) if self.check_nvenc() else (False,)):
                if self.cancel_render:
                    return None
                try:
                    cmd, seg_out = _build_seg_cmd(si, t0, t1, use_hw)
                except Exception:
                    return None
                pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      universal_newlines=True, errors='replace',
                                      creationflags=0x08000000, cwd=APP_DIR)
                self.active_processes.append(pr)
                # Watchdog: không có tiến độ mới trong 5 phút → kill (chống treo GPU)
                _last_beat = [time.time()]
                def _watchdog(p_=pr):
                    while p_.poll() is None:
                        time.sleep(15)
                        if self.cancel_render or time.time() - _last_beat[0] > 300:
                            try: p_.kill()
                            except Exception: pass
                            return
                threading.Thread(target=_watchdog, daemon=True).start()
                for line in pr.stderr:
                    if self.cancel_render:
                        try: pr.kill()
                        except Exception: pass
                        break
                    m_ = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
                    if m_:
                        _last_beat[0] = time.time()
                        h_, mn_, s_ = m_.groups()
                        with prog_lock:
                            prog[si] = min(int(h_) * 3600 + int(mn_) * 60 + float(s_), t1 - t0)
                        _update_ui()
                    else:
                        _st = line.strip()
                        if _st and not _st.startswith(('frame=', 'Input #', 'Output #',
                                                       'Stream #', 'Metadata:', 'Duration:',
                                                       'Press', 'Stream mapping',
                                                       'configuration:', 'built with',
                                                       'ffmpeg version', 'lib')):
                            err_tail.append(_st)
                            if len(err_tail) > 10:
                                err_tail.pop(0)
                pr.wait()
                if pr in self.active_processes:
                    self.active_processes.remove(pr)
                if (not self.cancel_render and pr.returncode == 0
                        and os.path.exists(seg_out) and os.path.getsize(seg_out) > 0):
                    with prog_lock:
                        prog[si] = t1 - t0
                    _update_ui()
                    return seg_out
            if not self.cancel_render:
                self.log(f"⚠ Khúc {si + 1}/{n_seg} lỗi (code {pr.returncode}).")
                for _e in err_tail[-4:]:
                    self.log(f"   ⚠ {_e}")
            return None

        def _run_audio():
            """Mix voice + BGM + SFX cho TOÀN video (audio-only → rất nhanh)."""
            a_out = os.path.join(TEMP_DIR, f"seg_{tag}_audio.m4a")
            voice_vol = task.get('voice_vol', 100) / 100.0
            bgm_vol = task.get('bgm_vol', 20) / 100.0
            cmd = [get_ffmpeg(), '-y', '-i', task['voice_file'],
                   '-stream_loop', '-1', '-i', chosen_bgm]
            idx = 2
            ap = []
            if cid_videos:
                ap.append(f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                          f"volume={voice_vol:.2f},apad=whole_dur={main_video_dur:.4f}[voice]")
                ap.append(f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                          f"volume={bgm_vol:.2f},atrim=0:{main_video_dur:.4f},"
                          f"afade=t=out:st={max(main_video_dur - 2, 0):.4f}:d=2[bgmn]")
            else:
                ap.append(f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                          f"volume={voice_vol:.2f}[voice]")
                ap.append(f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                          f"volume={bgm_vol:.2f}[bgmn]")
            sfx = []
            for f_, times in ((task.get('sfx_wipe_file', ''), pp.get('sfx_wipe_ms') or []),
                              (task.get('sfx_blink_file', ''), pp.get('sfx_blink_ms') or [])):
                if f_ and os.path.exists(f_):
                    for ts in times:
                        cmd += ['-i', f_]
                        ap.append(f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                                  f"channel_layouts=stereo,adelay={ts}|{ts}[sf{len(sfx)}]")
                        sfx.append(f"[sf{len(sfx)}]")
                        idx += 1
            adur = "longest" if cid_videos else "first"
            if sfx:
                ap.append(f"[voice][bgmn]amix=inputs=2:duration={adur}:dropout_transition=2[vb]")
                ap.append(f"[vb]{''.join(sfx)}amix=inputs={1 + len(sfx)}:duration={adur}:normalize=0[aout]")
            else:
                ap.append(f"[voice][bgmn]amix=inputs=2:duration={adur}:dropout_transition=2[aout]")
            cmd += ['-filter_complex', ";".join(ap), '-map', '[aout]',
                    '-c:a', 'aac', '-t', f'{main_video_dur:.4f}', a_out]
            r_ = subprocess.run(cmd, capture_output=True, creationflags=0x08000000)
            if r_.returncode == 0 and os.path.exists(a_out) and os.path.getsize(a_out) > 0:
                return a_out
            return None

        # --- 2) CHẠY: n_seg khúc video + 1 job audio, đồng thời ---
        from concurrent.futures import ThreadPoolExecutor
        t_start = time.time()
        with ThreadPoolExecutor(max_workers=workers + 1) as ex:
            fut_audio = ex.submit(_run_audio)
            seg_files = list(ex.map(_run_seg, range(n_seg)))
            audio_file = fut_audio.result()
        if self.cancel_render:
            return True   # đã hủy — không rơi về render lại
        if not all(seg_files) or not audio_file:
            self.log("⚠ Có khúc render lỗi → thử lại bằng render 1 lệnh.")
            return False

        # --- 3) NỐI KHÚC (copy) + GHÉP AUDIO ---
        lst = os.path.join(TEMP_DIR, f"seg_{tag}_list.txt")
        with open(lst, 'w', encoding='utf-8') as f_:
            for s_ in seg_files:
                f_.write("file '" + s_.replace("'", "'\\''") + "'\n")
        r_ = subprocess.run(
            [get_ffmpeg(), '-y', '-f', 'concat', '-safe', '0', '-i', lst,
             '-i', audio_file, '-map', '0:v', '-map', '1:a', '-c', 'copy',
             '-t', f'{main_video_dur:.4f}', render_target],
            capture_output=True, creationflags=0x08000000)
        for s_ in seg_files:
            try: os.remove(s_)
            except Exception: pass
        if r_.returncode != 0 or not os.path.exists(render_target) \
                or os.path.getsize(render_target) == 0:
            self.log("⚠ Nối khúc thất bại → thử lại bằng render 1 lệnh.")
            return False
        el = time.time() - t_start
        self.log(f"⚡ Pass 2 song song xong trong {el/60:.1f} phút "
                 f"({main_video_dur/max(el,1):.1f}x realtime).")

        # --- 4) CID + trạng thái hoàn thành ---
        if cid_videos and temp_main_video and os.path.exists(temp_main_video):
            self._concat_cid_jesus(task, temp_main_video, cid_videos, main_video_dur,
                                   cid_total_dur, output_file, final_filename,
                                   enc_args, out_dir, W, H, FPS)
        else:
            self.log(f"✅ HOÀN THÀNH (video nền): {final_filename}")
            self.after(0, lambda: self._set_render_status("✅ HOÀN THÀNH", "#1e5228"))
            self.after(0, self.update_progress_ui, 1.0, "Hoàn thành video (mode video nền)!")
        return True

    def _concat_cid_jesus(self, task, temp_main_video, cid_videos, main_video_dur,
                          cid_total_dur, output_file, final_filename, enc_args, out_dir, W, H, FPS):
        """Nối các video CID vào cuối video Jesus chính (mirror logic mode ảnh).
        Main video fade-out 1s cuối; mỗi CID scale + fade in/out; concat v+a."""
        self.after(0, self.set_render_stage, "cid", f"Nối {len(cid_videos)} video CID vào cuối...", 0.92)
        self.log(f"🎬 Nối {len(cid_videos)} video CID vào cuối...")

        concat_cmd = [get_ffmpeg(), '-y', '-i', temp_main_video]
        for cv in cid_videos:
            concat_cmd.extend(['-i', cv['path']])

        fc_parts = []
        n_inputs = 1 + len(cid_videos)
        fc_parts.append(f"[0:v]fade=t=out:st={max(main_video_dur-1, 0):.2f}:d=1[mv]")
        fc_parts.append("[0:a]acopy[ma]")
        for j in range(len(cid_videos)):
            idx = j + 1
            cd = cid_videos[j]['duration']
            fade_in = "fade=t=in:st=0:d=1,"
            fade_out = f"fade=t=out:st={max(cd-1, 0):.2f}:d=1" if j < len(cid_videos) - 1 else "null"
            fc_parts.append(
                f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps={FPS},{fade_in}{fade_out}[cv{j}]"
            )
            # Audio CID: dùng audio gốc nếu có; nếu video CID không có audio → tạo silent
            fc_parts.append(
                f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[ca{j}]"
            )
        interleaved = "[mv][ma]" + "".join(f"[cv{j}][ca{j}]" for j in range(len(cid_videos)))
        fc_parts.append(f"{interleaved}concat=n={n_inputs}:v=1:a=1[vout][aout]")
        fc_text = ';\n'.join(fc_parts)

        # Ghi ra file → -filter_complex_script (tránh dòng lệnh quá dài khi nhiều CID)
        fc_cid_script = os.path.join(TEMP_DIR, f"jesus_cidfc_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.txt")
        try:
            with open(fc_cid_script, 'w', encoding='utf-8') as ff:
                ff.write(fc_text)
            concat_cmd.extend(['-filter_complex_script', fc_cid_script])
        except Exception:
            concat_cmd.extend(['-filter_complex', fc_text])
        concat_cmd.extend(['-map', '[vout]', '-map', '[aout]']
                          + enc_args + ['-c:a', 'aac', output_file])

        debug_cid = os.path.join(out_dir, f"DEBUG_CID_JESUS_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.txt")
        try:
            with open(debug_cid, 'w', encoding='utf-8') as df:
                df.write("CID CONCAT (JESUS) COMMAND:\n")
                df.write(' '.join(f'"{a}"' if ' ' in a else a for a in concat_cmd) + '\n\n')
                df.write("FILTER:\n" + fc_text + '\n')
        except Exception:
            pass

        p_concat = subprocess.Popen(
            concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, errors='replace', creationflags=0x08000000
        )
        self.active_processes.append(p_concat)
        concat_errors = []
        total_dur = main_video_dur + cid_total_dur
        for line in p_concat.stderr:
            if self.cancel_render:
                try: p_concat.kill()
                except Exception: pass
                break
            match = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
            if match and total_dur > 0:
                h_t, m_t, s_t = match.groups()
                sec = int(h_t)*3600 + int(m_t)*60 + float(s_t)
                self.after(0, self.set_render_stage, "cid",
                           f"Đóng gói video cuối ({sec:.0f}/{total_dur:.0f}s)",
                           min(0.92 + (sec/total_dur)*0.08, 1.0))
            stripped = line.strip()
            if stripped and not any(stripped.startswith(p) for p in (
                    'frame=', 'Input #', 'Output #', 'Stream #', 'Metadata:', 'Duration:',
                    'Press', 'Stream mapping', 'configuration:', 'built with', 'ffmpeg version', 'lib')):
                concat_errors.append(stripped)

        p_concat.wait()
        if p_concat in self.active_processes: self.active_processes.remove(p_concat)
        try: os.remove(temp_main_video)
        except Exception: pass

        if self.cancel_render:
            return
        if p_concat.returncode == 0:
            self.log(f"✅ HOÀN THÀNH (Jesus + CID): {final_filename}")
            self.after(0, lambda: self._set_render_status("✅ HOÀN THÀNH", "#1e5228"))
            self.after(0, self.update_progress_ui, 1.0, "Hoàn thành video nền + CID!")
            try: os.remove(debug_cid)
            except Exception: pass
        else:
            self.log(f"❌ LỖI KHI NỐI VIDEO CID (code {p_concat.returncode})")
            relevant = [l for l in concat_errors if any(k in l.lower() for k in
                        ['error', 'failed', 'invalid', 'no such', 'cannot'])]
            if not relevant: relevant = concat_errors[-5:]
            for err in relevant[-5:]:
                self.log(f"   ⚠️ {err}")
            self.log(f"📄 Debug: {debug_cid}")

    def _wave_layer_count(self, task):
        """Số layer 'sóng radio' trong task (cần tách audio giọng để vẽ sóng)."""
        return sum(1 for l in (task.get('overlay_layers') or [])
                   if l.get('type') == 'waveform')

    def _build_overlay_video_filter(self, layers, start_idx, W, H, FPS, ass_escaped,
                                    base_label='0:v', out_label='vout', voice_file=None):
        """Ghép các layer overlay (logo/subscribe/phông xanh/sóng radio) vào chuỗi video.

        Trả về (input_args, vid_filter, next_idx):
          - input_args: list arg '-i' để extend vào lệnh ffmpeg (None nếu không có layer)
          - vid_filter: chuỗi filter biến [base_label] → [out_label] (đã gồm cả ass nếu
            ass_escaped truthy). None nếu không có layer → caller dùng đường cũ.
          - next_idx: input index kế tiếp.
        voice_file: đường dẫn file giọng đọc. Mỗi layer 'waveform' sẽ mở file này thành
                    1 INPUT RIÊNG (KHÔNG asplit chung để tránh đệm tràn bộ nhớ). Nếu None
                    → bỏ qua layer sóng (vd màn hình xem trước không có audio).
        Layer vẽ theo thứ tự danh sách (sau cùng nằm trên); phụ đề ASS luôn trên cùng."""
        valid = [l for l in (layers or [])
                 if l.get('type') == 'waveform' or (l.get('path') and os.path.exists(l['path']))]
        if not valid:
            return None, None, start_idx

        input_args = []
        idx = start_idx
        parts = [f"[{base_label}]format=yuv420p[ovbase];"]
        cur = "ovbase"

        for n, layer in enumerate(valid):
            w_px = max(2, int(round(W * float(layer.get('w', 0.2)))))
            h_px = max(2, int(round(H * float(layer.get('h', 0.2)))))
            x_px = int(round(W * float(layer.get('x', 0.0))))
            y_px = int(round(H * float(layer.get('y', 0.0))))
            op = float(layer.get('opacity', 1.0))
            ltype = layer.get('type')
            consumed_input = False

            if ltype == 'waveform':
                if not voice_file or not os.path.exists(voice_file):
                    continue  # không có nguồn audio → bỏ qua (giữ nguyên cur)
                # Mở file giọng thành INPUT RIÊNG cho sóng (độc lập với audio mix)
                input_args += ['-i', voice_file]
                color = layer.get('wave_color', '0x66E0FF')
                # showwaves cline (kiểu B) — sóng trải đều, phình/thu theo giọng
                chain = (f"[{idx}:a]showwaves=s={w_px}x{h_px}:mode=cline:colors={color}:"
                         f"scale=sqrt:draw=full,fps={FPS},format=rgba,"
                         f"colorkey=0x000000:0.20:0.06")
                if op < 0.99:
                    chain += f",colorchannelmixer=aa={op:.3f}"
                chain += f"[ov{n}];"
                parts.append(chain)
                consumed_input = True
            elif ltype == 'greenscreen':
                input_args += ['-stream_loop', '-1', '-i', layer['path']]
                # key_method='none' → KHÔNG tách nền, chèn video nguyên bản.
                # (Mặc định 'color' để tương thích layer cũ đã lưu.)
                if layer.get('key_method', 'color') == 'none':
                    chain = f"[{idx}:v]scale={w_px}:{h_px}"
                else:
                    color = layer.get('chroma_color', '0x00D800')
                    sim = float(layer.get('chroma_sim', 0.20))
                    blend = float(layer.get('chroma_blend', 0.10))
                    chain = f"[{idx}:v]chromakey={color}:{sim:.3f}:{blend:.3f},scale={w_px}:{h_px}"
                if op < 0.99:
                    chain += f",format=rgba,colorchannelmixer=aa={op:.3f}"
                chain += f"[ov{n}];"
                parts.append(chain)
                consumed_input = True
            else:
                # Ảnh: tách nền tuỳ chọn
                src = layer['path']
                method = layer.get('key_method', 'none')
                if method and method != 'none':
                    try:
                        keyed, _ = self._prepare_jesus_image(
                            src, method=method,
                            erode=int(layer.get('key_erode', 0)),
                            feather=float(layer.get('key_feather', 1.0)))
                        src = keyed
                    except Exception as e:
                        self.log(f"⚠️ Tách nền layer '{layer.get('name')}' lỗi ({e}) — dùng ảnh gốc.")
                input_args += ['-loop', '1', '-i', src]
                chain = f"[{idx}:v]scale={w_px}:{h_px},format=rgba"
                if op < 0.99:
                    chain += f",colorchannelmixer=aa={op:.3f}"
                chain += f"[ov{n}];"
                parts.append(chain)
                consumed_input = True

            parts.append(f"[{cur}][ov{n}]overlay={x_px}:{y_px}:shortest=0[ovr{n}];")
            cur = f"ovr{n}"
            if consumed_input:
                idx += 1

        if ass_escaped:
            parts.append(f"[{cur}]ass='{ass_escaped}',format=yuv420p[{out_label}];")
        else:
            parts.append(f"[{cur}]format=yuv420p[{out_label}];")
        return input_args, "".join(parts), idx

    def process_video(self):
        import stable_whisper

        task = self.current_task
        task_key = self._get_task_key(task)
        temp_ass = None
        temp_main_video = None
        
        try:
            if self.cancel_render: return

            # --- KIỂM TRA DỮ LIỆU PRE-PROCESS TỪ PIPELINE ---
            if task_key in self._preprocess_result:
                self.log("⚡ Pipeline: Dùng dữ liệu đã phân tích sẵn → Bỏ qua bước Whisper!")
                pp = self._preprocess_result.pop(task_key)
            else:
                pp = self._preprocess_task(task, silent=False)
                if pp is None: return

            # =========== NHÁNH MODE JESUS: render bằng filtergraph thuần FFmpeg ===========
            if task.get('render_mode') == 'jesus_split':
                temp_ass = pp.get('temp_ass')  # để finally dọn dẹp
                self._render_jesus_split(task, pp)
                return  # finally sẽ dọn temp_ass + gọi start_next_task
            # =========== HẾT NHÁNH JESUS ===========

            voice_dur = pp['voice_dur']; chosen_bgm = pp['chosen_bgm']
            available_overlays = pp['available_overlays']; temp_ass = pp['temp_ass']
            subtitle_events = pp['subtitle_events']; image_scenes = pp['image_scenes']
            sfx_wipe_timestamps_ms = pp['sfx_wipe_timestamps_ms']
            sfx_blink_timestamps_ms = pp['sfx_blink_timestamps_ms']
            final_images = pp['final_images']; image_effects = pp['image_effects']
            W = pp['W']; H = pp['H']
            
            # CID data
            cid_videos = pp.get('cid_videos', [])
            cid_total_dur = pp.get('cid_total_dur', 0)
            cid_extra_scenes = pp.get('cid_extra_scenes', [])
            cid_extra_images = pp.get('cid_extra_images', [])
            cid_extra_effects = pp.get('cid_extra_effects', [])
            
            # Gộp 5s extra scenes vào danh sách chính (cho chuyển tiếp mượt)
            if cid_videos:
                image_scenes = image_scenes + cid_extra_scenes
                final_images = final_images + cid_extra_images
                image_effects = image_effects + cid_extra_effects
            
            if self.cancel_render: return

            # --- DETECT GPU NVENC ---
            use_nvenc = self.check_nvenc()
            if use_nvenc:
                self.log("🚀 GPU NVIDIA detected → Dùng h264_nvenc để tăng tốc!")
                enc_args = self._nvenc_encoder_args()
            else:
                self.log("⚠️ Không tìm thấy GPU NVENC hoặc NVENC không hoạt động.")
                # Hỏi người dùng có muốn tiếp tục bằng CPU không
                user_choice = threading.Event()
                user_accepted = [False]  # dùng list để thay đổi từ trong lambda
                
                def ask_cpu_fallback():
                    result = messagebox.askyesno(
                        "Không tìm thấy GPU",
                        "⚠️ Không thể sử dụng GPU (NVENC) để render.\n\n"
                        "Có thể do:\n"
                        "• Driver NVIDIA quá cũ\n"
                        "• FFmpeg không tương thích phiên bản NVENC\n"
                        "• GPU đang bận hoặc không hỗ trợ\n\n"
                        "Bạn có muốn tiếp tục render bằng CPU (chậm hơn) không?",
                        icon='warning'
                    )
                    user_accepted[0] = result
                    user_choice.set()
                
                self.after(0, ask_cpu_fallback)
                user_choice.wait()  # Đợi người dùng chọn
                
                if not user_accepted[0]:
                    self.log("❌ Người dùng đã hủy render do không có GPU.")
                    self.render_queue.clear()
                    self.cancel_render = True
                    self.after(0, self.update_progress_ui, 0.0, "Đã hủy - Không có GPU")
                    self.after(0, lambda: self._set_render_status("❌ ĐÃ HỦY RENDER", "#8B0000"))
                    self.after(0, lambda: self.btn_cancel_render.pack_forget())
                    self.after(0, self.update_queue_ui)
                    self.is_rendering = False
                    return
                
                self.log("⚙️ Tiếp tục render bằng CPU (libx264)...")
                enc_args = ['-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p']

            self.after(0, self.set_render_stage, "video", "Khởi động render engine + FFmpeg NVENC...", 0.20)
            FPS = 30
            
            # Tính thời lượng phần chính (bao gồm 5s chuyển tiếp nếu có CID)
            CID_EXTRA = 5.0
            if cid_videos:
                main_video_dur = voice_dur + CID_EXTRA
                self.log(f"🎬 CID: {len(cid_videos)} video ({cid_total_dur:.0f}s) → chính {main_video_dur:.0f}s + nối CID sau")
            else:
                main_video_dur = voice_dur
            total_frames = int(main_video_dur * FPS)

            # --- XÂY DỰNG 1 LỆNH FFMPEG DUY NHẤT ---
            # output_folder ưu tiên; nếu rỗng → fallback theo thư mục voice.
            if task.get('output_folder'):
                out_dir = task['output_folder']
            else:
                out_dir = os.path.dirname(task['voice_file'])
            custom_name = task.get('custom_name', '').strip()
            if custom_name:
                safe_name = self._safe_filename(custom_name)
                final_filename = f"{safe_name}.mp4"
            else:
                voice_basename = os.path.splitext(os.path.basename(task['voice_file']))[0]
                final_filename = f"FINAL_{voice_basename}.mp4"
            output_file = os.path.join(out_dir, final_filename)
            
            # Nếu có CID → render chính vào file tạm, concat sau
            if cid_videos:
                temp_main_video = os.path.join(TEMP_DIR, f"main_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.mp4")
                render_output = temp_main_video
            else:
                temp_main_video = None
                render_output = output_file
            
            ass_escaped = self.escape_path_for_ffmpeg(temp_ass)

            # Input 0: rawvideo pipe | Input 1: voice | Input 2: bgm (loop)
            combined_cmd = [
                get_ffmpeg(), '-y',
                '-f', 'rawvideo', '-vcodec', 'rawvideo', '-s', f'{W}x{H}',
                '-pix_fmt', 'bgr24', '-r', str(FPS), '-i', '-',
                '-i', task['voice_file'],
                '-stream_loop', '-1', '-i', chosen_bgm
            ]
            input_idx = 3

            # SFX: chỉ add vào command nếu file thực sự tồn tại trên disk
            # (tránh FFmpeg fail "No such file or directory" khi user xóa SFX file)
            _wipe_path = task.get('sfx_wipe_file', '')
            _blink_path = task.get('sfx_blink_file', '')
            _wipe_exists = bool(_wipe_path) and os.path.exists(_wipe_path)
            _blink_exists = bool(_blink_path) and os.path.exists(_blink_path)
            
            if _wipe_path and not _wipe_exists:
                self.log(f"⚠ SFX wipe không tồn tại, bỏ qua: {_wipe_path}")
            if _blink_path and not _blink_exists:
                self.log(f"⚠ SFX blink không tồn tại, bỏ qua: {_blink_path}")
            
            has_wipe_sfx = bool(_wipe_exists and sfx_wipe_timestamps_ms)
            has_blink_sfx = bool(_blink_exists and sfx_blink_timestamps_ms)

            wipe_input_idx = -1
            if has_wipe_sfx:
                combined_cmd.extend(['-i', task['sfx_wipe_file']])
                wipe_input_idx = input_idx
                input_idx += 1

            blink_input_idx = -1
            if has_blink_sfx:
                combined_cmd.extend(['-i', task['sfx_blink_file']])
                blink_input_idx = input_idx
                input_idx += 1

            # --- LAYER OVERLAY (logo / subscribe / phông xanh / sóng radio) ---
            # Sóng radio đọc file giọng thành input RIÊNG (không asplit → không tràn RAM)
            nwave = self._wave_layer_count(task)
            ov_inputs, ov_vid_filter, input_idx = self._build_overlay_video_filter(
                task.get('overlay_layers'), input_idx, W, H, FPS, ass_escaped,
                voice_file=task.get('voice_file'))
            if ov_inputs is not None:
                combined_cmd.extend(ov_inputs)
                n_lyr = len(task.get('overlay_layers') or [])
                self.log(f"🧩 Ghép {n_lyr} thành phần overlay vào video"
                         + (f" (gồm {nwave} sóng radio)." if nwave else "."))
            # Chuỗi video: dùng overlay nếu có, ngược lại giữ nguyên đường cũ
            _vid_head = ov_vid_filter or f"[0:v]ass='{ass_escaped}',format=yuv420p[vout];"

            # Lấy volume từ task (% → hệ số)
            voice_vol = task.get('voice_vol', 100) / 100.0
            bgm_vol = task.get('bgm_vol', 20) / 100.0

            if cid_videos:
                # CID mode: voice padded + BGM extended with fade out
                filter_complex = _vid_head
                filter_complex += f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={voice_vol:.2f},apad=whole_dur={main_video_dur:.4f}[voice_pad];"
                filter_complex += f"[2:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={bgm_vol:.2f},atrim=0:{main_video_dur:.4f},afade=t=out:st={max(main_video_dur-2, 0):.4f}:d=2[bgm_ext];"
            else:
                # Normal mode
                filter_complex = _vid_head
                filter_complex += f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={voice_vol:.2f}[voice_norm];"
                filter_complex += f"[2:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume={bgm_vol:.2f}[bgm_norm];"

            sfx_mix_parts = ""
            sfx_count = 0

            if has_wipe_sfx:
                num_w = len(sfx_wipe_timestamps_ms)
                filter_complex += f"[{wipe_input_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
                if num_w > 1:
                    filter_complex += f",asplit={num_w}"
                    for i in range(num_w): filter_complex += f"[wipe_split_{i}]"
                    filter_complex += ";"
                    for i, ts in enumerate(sfx_wipe_timestamps_ms):
                        filter_complex += f"[wipe_split_{i}]adelay={ts}|{ts}[sfx_w_{i}];"
                        sfx_mix_parts += f"[sfx_w_{i}]"
                        sfx_count += 1
                else:
                    filter_complex += f"[wipe_split_0];[wipe_split_0]adelay={sfx_wipe_timestamps_ms[0]}|{sfx_wipe_timestamps_ms[0]}[sfx_w_0];"
                    sfx_mix_parts += "[sfx_w_0]"
                    sfx_count += 1

            if has_blink_sfx:
                num_b = len(sfx_blink_timestamps_ms)
                filter_complex += f"[{blink_input_idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
                if num_b > 1:
                    filter_complex += f",asplit={num_b}"
                    for i in range(num_b): filter_complex += f"[blink_split_{i}]"
                    filter_complex += ";"
                    for i, ts in enumerate(sfx_blink_timestamps_ms):
                        filter_complex += f"[blink_split_{i}]adelay={ts}|{ts}[sfx_b_{i}];"
                        sfx_mix_parts += f"[sfx_b_{i}]"
                        sfx_count += 1
                else:
                    filter_complex += f"[blink_split_0];[blink_split_0]adelay={sfx_blink_timestamps_ms[0]}|{sfx_blink_timestamps_ms[0]}[sfx_b_0];"
                    sfx_mix_parts += "[sfx_b_0]"
                    sfx_count += 1

            if sfx_count > 0:
                filter_complex += f"{sfx_mix_parts}amix=inputs={sfx_count}[sfx_mix_raw];[sfx_mix_raw]volume={float(sfx_count)}[sfx_all];"
                if cid_videos:
                    filter_complex += f"[voice_pad][bgm_ext][sfx_all]amix=inputs=3:duration=longest:dropout_transition=2,volume=3.0[aout]"
                else:
                    filter_complex += f"[voice_norm][bgm_norm][sfx_all]amix=inputs=3:duration=first:dropout_transition=2,volume=3.0[aout]"
            else:
                if cid_videos:
                    filter_complex += f"[voice_pad][bgm_ext]amix=inputs=2:duration=longest:dropout_transition=2[aout]"
                else:
                    filter_complex += f"[voice_norm][bgm_norm]amix=inputs=2:duration=first:dropout_transition=2[aout]"

            combined_cmd.extend([
                '-filter_complex', filter_complex,
                '-map', '[vout]', '-map', '[aout]'
            ] + enc_args + [
                '-c:a', 'aac', '-t', str(main_video_dur), render_output
            ])

            # Capture stderr để có error message khi FFmpeg fail
            process_main = subprocess.Popen(
                combined_cmd, stdin=subprocess.PIPE,
                stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                creationflags=0x08000000
            )
            self.active_processes.append(process_main)
            
            # Thread riêng đọc stderr (tránh block buffer khi FFmpeg in nhiều)
            _ffmpeg_stderr_lines = []
            def _drain_stderr():
                try:
                    for line in process_main.stderr:
                        decoded = line.decode('utf-8', errors='replace').strip()
                        if decoded:
                            _ffmpeg_stderr_lines.append(decoded)
                            # Giới hạn để không tốn quá nhiều RAM
                            if len(_ffmpeg_stderr_lines) > 500:
                                _ffmpeg_stderr_lines.pop(0)
                except Exception:
                    pass
            import threading as _th
            _stderr_thread = _th.Thread(target=_drain_stderr, daemon=True)
            _stderr_thread.start()

            # --- PRE-LOAD ẢNH BẰNG THREAD POOL ---
            from concurrent.futures import ThreadPoolExecutor
            loaded_images = {}
            preload_futures = {}
            img_executor = ThreadPoolExecutor(max_workers=2)
            
            def _load_and_prepare(idx):
                return self.prepare_image(final_images[idx], W, H)
            
            def preload_ahead(current_idx):
                """Pre-load 2 ảnh tiếp theo trong background"""
                for ahead in [current_idx + 1, current_idx + 2]:
                    if ahead < len(final_images) and ahead not in loaded_images and ahead not in preload_futures:
                        preload_futures[ahead] = img_executor.submit(_load_and_prepare, ahead)
            
            def load_img_cache(idx):
                if idx not in loaded_images:
                    if idx in preload_futures:
                        # Ảnh đã được pre-load xong hoặc đang load → chờ kết quả
                        loaded_images[idx] = preload_futures.pop(idx).result()
                    else:
                        # Chưa pre-load → load trực tiếp
                        loaded_images[idx] = _load_and_prepare(idx)
                    # Dọn ảnh cũ để tiết kiệm RAM
                    keys_to_delete = [k for k in loaded_images if k < idx - 1]
                    for k in keys_to_delete: loaded_images.pop(k, None)
                # Trigger pre-load ảnh tiếp theo
                preload_ahead(idx)
                return loaded_images[idx]
            
            # Pre-load ảnh đầu tiên ngay
            preload_ahead(-1)

            # Khởi tạo VideoCapture cho Overlay — skip resize nếu đã đúng 1920x1080
            cap_overlay = None
            overlay_skip_resize = False
            if available_overlays:
                chosen_ovl = random.choice(available_overlays)
                cap_overlay = cv2.VideoCapture(chosen_ovl)
                # Kiểm tra đã đúng resolution chưa
                if cap_overlay.isOpened():
                    ovl_w = int(cap_overlay.get(cv2.CAP_PROP_FRAME_WIDTH))
                    ovl_h = int(cap_overlay.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    overlay_skip_resize = (ovl_w == W and ovl_h == H)
            last_scene_idx = -1

            black_frame = np.zeros((H, W, 3), dtype=np.uint8)
            black_bytes = black_frame.tobytes()  # 1B: Pre-compute 1 lần, dùng lại cho mọi frame đen
            
            # --- PIPELINE: Pre-process video tiếp theo trong nền ---
            if self.render_queue and not self.cancel_render:
                self._preprocess_thread = threading.Thread(
                    target=self._preprocess_next_in_background, daemon=True
                )
                self._preprocess_thread.start()
            
            # --- 1A: DOUBLE-BUFFER: Tách vẽ frame và ghi pipe ---
            write_queue = queue_module.Queue(maxsize=30)  # Buffer 30 frames
            write_error = [False]

            def _writer_thread():
                """Thread riêng chuyên ghi bytes vào pipe FFmpeg."""
                try:
                    while True:
                        data = write_queue.get()
                        if data is None: break  # Tín hiệu dừng
                        if self.cancel_render: break
                        process_main.stdin.write(data)
                except Exception:
                    write_error[0] = True
            
            writer = threading.Thread(target=_writer_thread, daemon=True)
            writer.start()

            def _q_put(data):
                """Đẩy frame vào queue KHÔNG kẹt vĩnh viễn: nếu FFmpeg chết giữa chừng
                (vd hết dung lượng ổ) → writer dừng → queue đầy → put thường sẽ treo app.
                Trả về False khi cần dừng render."""
                while not self.cancel_render and not write_error[0]:
                    try:
                        write_queue.put(data, timeout=1.0)
                        return True
                    except queue_module.Full:
                        continue
                return False
            
            # --- 1C: Biến theo dõi frame skip ---
            SKIP_INTERVAL = 2  # Chỉ tính warpAffine mỗi 2 frame
            last_rendered_frame = None
            last_rendered_scene_idx = -1
            
            # --- 1D: Pre-compute scene start times cho binary search ---
            scene_starts = [s['start'] for s in image_scenes]
            
            start_render_time = time.time()

            for f in range(total_frames):
                if self.cancel_render or write_error[0]: break
                t = f / FPS
                
                # 1D: Binary search thay vì linear scan
                bs_idx = bisect.bisect_right(scene_starts, t) - 1
                active_idx = -1
                if bs_idx >= 0 and bs_idx < len(image_scenes):
                    s = image_scenes[bs_idx]
                    if s['start'] <= t < s['scene_end']:
                        active_idx = bs_idx

                # TỰ ĐỘNG THAY ĐỔI OVERLAY SAU KHI HẾT MÀN HÌNH ĐEN
                if active_idx != last_scene_idx:
                    if last_scene_idx != -1:
                        prev_scene = image_scenes[last_scene_idx]
                        curr_scene = image_scenes[active_idx] if active_idx != -1 else None
                        if prev_scene['is_segment_ending'] and curr_scene and not curr_scene['is_segment_ending']:
                            if available_overlays:
                                if cap_overlay: cap_overlay.release()
                                chosen_ovl = random.choice(available_overlays)
                                cap_overlay = cv2.VideoCapture(chosen_ovl)
                                if cap_overlay.isOpened():
                                    ovl_w = int(cap_overlay.get(cv2.CAP_PROP_FRAME_WIDTH))
                                    ovl_h = int(cap_overlay.get(cv2.CAP_PROP_FRAME_HEIGHT))
                                    overlay_skip_resize = (ovl_w == W and ovl_h == H)
                                else:
                                    overlay_skip_resize = False
                    last_scene_idx = active_idx
                    last_rendered_frame = None  # Reset skip cache khi đổi scene

                is_blackscreen_now = False
                is_transition = False

                if active_idx == -1:
                    # 1B: Ghi black_bytes trực tiếp, không tạo frame mới
                    if not _q_put(black_bytes): break
                    is_blackscreen_now = True
                    
                    if f % 30 == 0:
                        if f > 0:
                            fps_render = f / (time.time() - start_render_time)
                            msg = f"Render frame {f}/{total_frames} ({fps_render:.1f} fps) — ETA {self.format_eta((total_frames - f) / fps_render)}"
                        else: msg = "Đang khởi động Render Engine..."
                        self.after(0, self.set_render_stage, "video", msg, 0.20 + (f / total_frames) * 0.75)
                    continue
                
                curr_scene = image_scenes[active_idx]
                
                if curr_scene['is_segment_ending']:
                    # Kiểm tra có đang transition không
                    overlap_time = 1.0
                    time_left = curr_scene['scene_end'] - t
                    if time_left < overlap_time and active_idx < len(image_scenes) - 1:
                        is_transition = True
                    else:
                        # 1B: Black screen thuần, dùng lại bytes
                        if not _q_put(black_bytes): break
                        is_blackscreen_now = True
                        if f % 30 == 0:
                            if f > 0:
                                fps_render = f / (time.time() - start_render_time)
                                msg = f"Render frame {f}/{total_frames} ({fps_render:.1f} fps) — ETA {self.format_eta((total_frames - f) / fps_render)}"
                            else: msg = "Đang khởi động Render Engine..."
                            self.after(0, self.set_render_stage, "video", msg, 0.20 + (f / total_frames) * 0.75)
                        continue
                
                # Crossfade CĂN GIỮA ranh giới scene (= đầu câu mới):
                #   nửa đầu (0.5s cuối scene trước) blend curr→next, nửa sau (0.5s đầu
                #   scene mới) blend prev→curr → khoảnh khắc đổi ảnh rơi ĐÚNG đầu câu mới.
                overlap_time = 1.0
                half = 0.5
                trans_in = False        # crossfade từ ảnh TRƯỚC (nửa đầu scene mới)
                trans_out = False       # crossfade sang ảnh SAU (nửa cuối scene này)
                trans_out_black = False # fade sang ĐEN trước màn hình đen kế tiếp
                if not is_transition and not curr_scene['is_segment_ending']:
                    elapsed = t - curr_scene['start']
                    time_left = curr_scene['scene_end'] - t
                    n_sc = len(image_scenes)
                    next_bs = (active_idx < n_sc - 1 and image_scenes[active_idx + 1]['is_segment_ending'])
                    prev_bs = (active_idx > 0 and image_scenes[active_idx - 1]['is_segment_ending'])
                    if active_idx < n_sc - 1 and next_bs and time_left < overlap_time:
                        trans_out_black = True; is_transition = True
                    elif active_idx < n_sc - 1 and (not next_bs) and time_left < half:
                        trans_out = True; is_transition = True
                    elif active_idx > 0 and (not prev_bs) and elapsed < half:
                        trans_in = True; is_transition = True

                # --- VẼ FRAME ---
                if is_transition:
                    # Transition luôn vẽ mới (không skip)
                    last_rendered_frame = None
                    
                    if curr_scene['is_segment_ending']:
                        next_scene = image_scenes[active_idx + 1]
                        blend_factor = 1.0 - ((curr_scene['scene_end'] - t) / overlap_time)
                        bs_effect = curr_scene.get('bs_effect', 'wipe')
                        next_duration = next_scene['scene_end'] - next_scene['start']
                        frame2 = self.get_animated_frame(load_img_cache(active_idx+1), 0.0, next_duration, image_effects[active_idx+1], W, H)

                        if bs_effect == 'wipe':
                            frame = black_frame.copy()
                            x_split = int(W * (1.0 - blend_factor))
                            if x_split < W:
                                frame[:, x_split:] = frame2[:, x_split:]
                                cv2.line(frame, (x_split, 0), (x_split, H), (255, 255, 255), 3)
                        elif bs_effect == 'blink':
                            frame = frame2.copy()
                            open_h = int((H / 2) * blend_factor)
                            top_bar = (H // 2) - open_h
                            bot_bar = (H // 2) + open_h
                            if top_bar > 0:
                                cv2.rectangle(frame, (0, 0), (W, top_bar), (0, 0, 0), -1)
                                cv2.line(frame, (0, top_bar), (W, top_bar), (255, 255, 255), 2)
                            if bot_bar < H:
                                cv2.rectangle(frame, (0, bot_bar), (W, H), (0, 0, 0), -1)
                                cv2.line(frame, (0, bot_bar), (W, bot_bar), (255, 255, 255), 2)
                    else:
                        base = self.get_animated_frame(load_img_cache(active_idx), t - curr_scene['start'], curr_scene['scene_end'] - curr_scene['start'], image_effects[active_idx], W, H)
                        if trans_out_black:
                            # fade sang đen (giữ nguyên hiệu ứng vào màn hình đen)
                            blend_factor = 1.0 - ((curr_scene['scene_end'] - t) / overlap_time)
                            frame = cv2.addWeighted(base, 1.0 - blend_factor, black_frame, blend_factor, 0)
                        elif trans_out:
                            # nửa ĐẦU crossfade (0.5s cuối scene này): curr → next, blend 0→0.5
                            next_scene = image_scenes[active_idx + 1]
                            next_duration = next_scene['scene_end'] - next_scene['start']
                            frame2 = self.get_animated_frame(load_img_cache(active_idx+1), 0.0, next_duration, image_effects[active_idx+1], W, H)
                            blend_factor = (t - (curr_scene['scene_end'] - half)) / overlap_time
                            frame = cv2.addWeighted(base, 1.0 - blend_factor, frame2, blend_factor, 0)
                        else:
                            # nửa SAU crossfade (0.5s đầu scene mới): prev → curr, blend 0.5→1.0
                            prev_idx = active_idx - 1
                            prev_scene = image_scenes[prev_idx]
                            prev_duration = prev_scene['scene_end'] - prev_scene['start']
                            prev_frame = self.get_animated_frame(load_img_cache(prev_idx), prev_duration, prev_duration, image_effects[prev_idx], W, H)
                            blend_factor = 0.5 + (t - curr_scene['start']) / overlap_time
                            frame = cv2.addWeighted(prev_frame, 1.0 - blend_factor, base, blend_factor, 0)
                else:
                    # --- 1C: SKIP WARPAFFINE mỗi 2 frame cho scene bình thường ---
                    if last_rendered_frame is not None and last_rendered_scene_idx == active_idx and f % SKIP_INTERVAL != 0:
                        frame = last_rendered_frame
                    else:
                        frame = self.get_animated_frame(load_img_cache(active_idx), t - curr_scene['start'], curr_scene['scene_end'] - curr_scene['start'], image_effects[active_idx], W, H)
                        last_rendered_frame = frame
                        last_rendered_scene_idx = active_idx

                # Overlay
                if cap_overlay and not is_blackscreen_now:
                    ret, overlay_frame = cap_overlay.read()
                    if not ret: cap_overlay.set(cv2.CAP_PROP_POS_FRAMES, 0); ret, overlay_frame = cap_overlay.read()
                    if ret:
                        if overlay_skip_resize:
                            frame = cv2.addWeighted(frame, 1.0, overlay_frame, 0.7, 0)
                        else:
                            frame = cv2.addWeighted(frame, 1.0, cv2.resize(overlay_frame, (W, H)), 0.7, 0)

                # 1A: Đẩy vào queue thay vì ghi trực tiếp (không kẹt nếu FFmpeg chết)
                if not _q_put(frame.tobytes()): break

                if f % 30 == 0:
                    if f > 0:
                        fps_render = f / (time.time() - start_render_time)
                        msg = f"Render frame {f}/{total_frames} ({fps_render:.1f} fps) — ETA {self.format_eta((total_frames - f) / fps_render)}"
                    else: msg = "Đang khởi động Render Engine..."
                    self.after(0, self.set_render_stage, "video", msg, 0.20 + (f / total_frames) * 0.75)

            # Kết thúc writer thread
            try:
                write_queue.put(None, timeout=3)
            except queue_module.Full:
                pass
            writer.join(timeout=10)
            
            process_main.stdin.close()
            process_main.wait()
            if process_main in self.active_processes: self.active_processes.remove(process_main)
            if cap_overlay: cap_overlay.release()
            img_executor.shutdown(wait=False)

            if not self.cancel_render and process_main.returncode == 0:
                # --- CID CONCAT: Nối video CID vào cuối ---
                if cid_videos and temp_main_video and os.path.exists(temp_main_video):
                    self.after(0, self.set_render_stage, "cid", f"Nối {len(cid_videos)} video CID vào cuối...", 0.92)
                    self.log(f"🎬 Nối {len(cid_videos)} video CID vào cuối...")
                    
                    concat_cmd = [get_ffmpeg(), '-y', '-i', temp_main_video]
                    for cv in cid_videos:
                        concat_cmd.extend(['-i', cv['path']])
                    
                    fc_parts = []
                    n_inputs = 1 + len(cid_videos)
                    
                    # Main video: fade out 1s cuối
                    fc_parts.append(
                        f"[0:v]fade=t=out:st={max(main_video_dur-1, 0):.2f}:d=1[mv]"
                    )
                    fc_parts.append(f"[0:a]acopy[ma]")
                    
                    # Scale + fade CID videos, thêm silent audio nếu thiếu
                    for j in range(len(cid_videos)):
                        idx = j + 1
                        cd = cid_videos[j]['duration']
                        # Video: scale + fade in 1s đầu, fade out 1s cuối (trừ video cuối)
                        fade_in = "fade=t=in:st=0:d=1,"
                        fade_out = f"fade=t=out:st={max(cd-1, 0):.2f}:d=1" if j < len(cid_videos) - 1 else "null"
                        fc_parts.append(
                            f"[{idx}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                            f"crop={W}:{H},setsar=1,fps={FPS},{fade_in}{fade_out}[cv{j}]"
                        )
                        # Audio: dùng audio gốc nếu có, nếu không tạo silent
                        fc_parts.append(
                            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[ca{j}]"
                        )
                    
                    # Concat: main + tất cả CID (xen kẽ [video][audio] theo segment)
                    interleaved = "[mv][ma]" + "".join(f"[cv{j}][ca{j}]" for j in range(len(cid_videos)))
                    fc_parts.append(f"{interleaved}concat=n={n_inputs}:v=1:a=1[vout][aout]")
                    
                    fc_text = ';\n'.join(fc_parts)
                    
                    concat_cmd.extend([
                        '-filter_complex', fc_text,
                        '-map', '[vout]', '-map', '[aout]'
                    ] + enc_args + ['-c:a', 'aac', output_file])
                    
                    # Debug: ghi command ra log
                    debug_cid = os.path.join(out_dir, f"DEBUG_CID_{hashlib.md5(task['voice_file'].encode()).hexdigest()[:8]}.txt")
                    with open(debug_cid, 'w', encoding='utf-8') as df:
                        df.write("CID CONCAT COMMAND:\n")
                        df.write(' '.join(f'"{a}"' if ' ' in a else a for a in concat_cmd) + '\n\n')
                        df.write("FILTER:\n" + fc_text + '\n')
                    
                    p_concat = subprocess.Popen(
                        concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True, errors='replace', creationflags=0x08000000
                    )
                    self.active_processes.append(p_concat)
                    
                    concat_errors = []
                    for line in p_concat.stderr:
                        if self.cancel_render: p_concat.kill(); break
                        match = re.search(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})", line)
                        if match:
                            h_t, m_t, s_t = match.groups()
                            sec = int(h_t)*3600 + int(m_t)*60 + float(s_t)
                            total_dur = main_video_dur + cid_total_dur
                            if total_dur > 0:
                                self.after(0, self.set_render_stage, "cid",
                                            f"Đóng gói video cuối ({sec:.0f}/{total_dur:.0f}s)",
                                            min(0.92 + (sec/total_dur)*0.08, 1.0))
                        # Thu thập lỗi
                        stripped = line.strip()
                        if stripped and not any(stripped.startswith(p) for p in ('frame=', 'Input #', 'Output #', 'Stream #', 'Metadata:', 'Duration:', 'Press', 'Stream mapping', 'configuration:', 'built with', 'ffmpeg version', 'lib')):
                            concat_errors.append(stripped)
                    
                    p_concat.wait()
                    if p_concat in self.active_processes: self.active_processes.remove(p_concat)
                    
                    # Dọn temp
                    try: os.remove(temp_main_video)
                    except: pass
                    
                    if not self.cancel_render and p_concat.returncode == 0:
                        self.log(f"✅ HOÀN THÀNH (+ CID): {final_filename}")
                        self._set_render_status("✅ HOÀN THÀNH", "#1e5228")
                        # Dọn debug file khi thành công
                        try: os.remove(debug_cid)
                        except: pass
                    elif not self.cancel_render:
                        self.log(f"❌ LỖI KHI NỐI VIDEO CID (code {p_concat.returncode})")
                        # Hiện lỗi quan trọng
                        relevant = [l for l in concat_errors if any(k in l.lower() for k in ['error', 'failed', 'invalid', 'no such', 'cannot'])]
                        if not relevant: relevant = concat_errors[-5:]
                        for err in relevant[-5:]:
                            self.log(f"   ⚠️ {err}")
                        self.log(f"📄 Debug: {debug_cid}")
                        self.log(f"💡 Copy file debug trên gửi cho dev!")
                else:
                    # Không có CID → xong luôn
                    self.log(f"✅ HOÀN THÀNH: {final_filename}")
                    self._set_render_status("✅ HOÀN THÀNH", "#1e5228")
            elif not self.cancel_render:
                # ❌ FFMPEG RETURN CODE != 0 → render fail
                returncode = process_main.returncode
                stderr_text = "\n".join(_ffmpeg_stderr_lines) if _ffmpeg_stderr_lines else ""
                
                self.log(f"❌ LỖI KHI XUẤT VIDEO: {final_filename}")
                self.log(f"   FFmpeg return code: {returncode}")
                
                # Hiện các dòng error quan trọng từ stderr
                if stderr_text:
                    error_lines = [l for l in _ffmpeg_stderr_lines
                                    if any(k in l.lower() for k in
                                            ['error', 'failed', 'invalid', 'no such',
                                             'cannot', 'unable to', 'permission denied',
                                             'not found', 'unrecognized'])]
                    if not error_lines:
                        # Fallback: 10 dòng cuối
                        error_lines = _ffmpeg_stderr_lines[-10:]
                    self.log(f"   FFmpeg stderr ({len(error_lines)} dòng lỗi quan trọng):")
                    for err in error_lines[-10:]:
                        self.log(f"   ⚠ {err}")
                
                # Lưu file debug đầy đủ qua debug_logger
                if DEBUG_LOGGER_AVAILABLE:
                    try:
                        debug_path = debug_logger.save_render_failure(
                            operation=f"render_main_{final_filename}",
                            command=combined_cmd,
                            stderr_text=stderr_text,
                            extra_context={
                                "task_voice_file": task.get('voice_file', ''),
                                "task_subtitle_style": task.get('subtitle_style', ''),
                                "task_language": task.get('language', ''),
                                "output_file": output_file,
                                "main_video_dur_sec": main_video_dur,
                                "total_frames": total_frames,
                                "ffmpeg_returncode": returncode,
                            }
                        )
                        if debug_path:
                            self.log(f"📄 File debug đã lưu: {debug_path}")
                            self.log(f"💡 Copy file này gửi cho dev để debug!")
                    except Exception as _dbg_err:
                        self.log(f"⚠ Không lưu được debug file: {_dbg_err}")
                
                # Dọn temp nếu lỗi
                if temp_main_video and os.path.exists(temp_main_video):
                    try: os.remove(temp_main_video)
                    except: pass

        except Exception as e:
            if not self.cancel_render:
                # In full traceback ra log
                tb_text = traceback.format_exc()
                self.log(f"❌ Lỗi Hệ Thống: {type(e).__name__}: {e}")
                # In ngắn gọn 5 dòng cuối stack trace cho user thấy
                tb_lines = tb_text.strip().split("\n")
                for line in tb_lines[-8:]:
                    self.log(f"   {line}")
                # Gửi báo cáo lỗi render về Discord của dev
                _ctx = f"render: {os.path.basename((self.current_task or {}).get('voice_file',''))}"
                self._report_error_remote(f"Lỗi render: {type(e).__name__}: {e}",
                                          str(e), tb_text, _ctx)
                
                # Ghi đầy đủ vào debug_logger
                if DEBUG_LOGGER_AVAILABLE:
                    try:
                        ctx = {
                            "current_task": str(self.current_task)[:200] if self.current_task else 'None',
                        }
                        debug_path = debug_logger.save_render_failure(
                            operation="render_exception",
                            exception=e,
                            extra_context=ctx
                        )
                        if debug_path:
                            self.log(f"📄 Full traceback đã lưu: {debug_path}")
                            self.log(f"💡 Gửi file này cho dev để debug!")
                        debug_logger.log_exception("process_video crashed", exc=e)
                    except Exception:
                        pass
        finally:
            if temp_ass and os.path.exists(temp_ass):
                try: os.remove(temp_ass)
                except: pass
            if temp_main_video and os.path.exists(temp_main_video):
                try: os.remove(temp_main_video)
                except: pass
            # Xóa ngay toàn bộ file tạm của video vừa xong (giải phóng ổ C tức thì)
            self._cleanup_task_temp(self.current_task)
            if self.cancel_render:
                self._preprocess_result.clear()
            if not self.cancel_render: self.after(2000, self.start_next_task)

if __name__ == "__main__":
    app = VideoGeneratorApp()
    app.mainloop()