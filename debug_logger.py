"""debug_logger.py — Centralized error logger cho Chosen One Batch Render Engine

Mục đích: bất kỳ lỗi/exception nào xảy ra trong app sẽ được ghi lại đầy đủ
(message + stack trace + context) vào một file để user có thể gửi cho dev debug.

Tính năng:
- Session-based debug folder: mỗi lần chạy app tạo 1 folder riêng
- Master error log: tất cả lỗi → app_errors.log (append, rotate khi >5MB)
- Per-failure debug file: lỗi nghiêm trọng (render fail, TTS fail) tạo file riêng
  chứa: timestamp + context + full traceback + command + stderr (nếu có)
- System info snapshot: ghi lại OS, Python, GPU, FFmpeg version khi khởi động
- Export bundle: nén toàn bộ session logs thành 1 file zip để user gửi dev
"""

import os
import sys
import json
import time
import traceback
import platform
import subprocess
import zipfile
import logging
from datetime import datetime
from pathlib import Path


# Sẽ được set bởi setup_debug_logger() khi app khởi động
DEBUG_DIR = None
SESSION_DIR = None
MASTER_LOG = None
_session_start_iso = None


def setup_debug_logger(app_dir: str):
    """Khởi tạo hệ thống debug log. Gọi 1 lần khi app start.

    Tạo cấu trúc:
        APP_DIR/debug_logs/
            ├── app_errors.log               ← master log, append-only, rotate >5MB
            ├── session_YYYYMMDD_HHMMSS/     ← folder cho session này
            │   ├── system_info.json         ← OS, Python, GPU, FFmpeg
            │   ├── errors.log               ← log riêng cho session
            │   └── render_fail_*.txt        ← debug files cho từng lần render fail
            └── (older sessions...)
    """
    global DEBUG_DIR, SESSION_DIR, MASTER_LOG, _session_start_iso

    DEBUG_DIR = os.path.join(app_dir, "debug_logs")
    os.makedirs(DEBUG_DIR, exist_ok=True)

    # Rotate master log nếu > 5MB
    MASTER_LOG = os.path.join(DEBUG_DIR, "app_errors.log")
    if os.path.exists(MASTER_LOG) and os.path.getsize(MASTER_LOG) > 5 * 1024 * 1024:
        try:
            archive = MASTER_LOG + f".{int(time.time())}.old"
            os.rename(MASTER_LOG, archive)
        except OSError:
            pass

    # Tạo session folder
    _session_start_iso = datetime.now().strftime("%Y%m%d_%H%M%S")
    SESSION_DIR = os.path.join(DEBUG_DIR, f"session_{_session_start_iso}")
    os.makedirs(SESSION_DIR, exist_ok=True)

    # Cleanup các session cũ (giữ lại 10 session gần nhất)
    _cleanup_old_sessions(keep=10)

    # Ghi system info snapshot
    _write_system_info()

    log_event("INFO", "=== App started ===")
    return SESSION_DIR


def _cleanup_old_sessions(keep: int = 10):
    """Xóa các folder session cũ, chỉ giữ N folder mới nhất."""
    if not DEBUG_DIR or not os.path.exists(DEBUG_DIR):
        return
    try:
        sessions = sorted(
            [d for d in os.listdir(DEBUG_DIR)
             if d.startswith("session_") and os.path.isdir(os.path.join(DEBUG_DIR, d))],
            reverse=True
        )
        for old in sessions[keep:]:
            try:
                import shutil
                shutil.rmtree(os.path.join(DEBUG_DIR, old), ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


def _write_system_info():
    """Ghi system info snapshot (1 lần khi start session)."""
    info = {
        "session_start": _session_start_iso,
        "platform": platform.platform(),
        "python_version": sys.version,
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "frozen_exe": getattr(sys, 'frozen', False),
    }

    # Optional: try get GPU info (NVIDIA)
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if sys.platform.startswith("win") else 0
        )
        if r.returncode == 0:
            info["gpu"] = r.stdout.strip()
    except Exception:
        info["gpu"] = "unavailable"

    # Optional: FFmpeg version
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5,
            creationflags=0x08000000 if sys.platform.startswith("win") else 0
        )
        if r.returncode == 0:
            # Lấy dòng đầu: "ffmpeg version X.Y.Z ..."
            info["ffmpeg"] = r.stdout.split("\n")[0]
    except Exception:
        info["ffmpeg"] = "unavailable"

    # Optional: torch/CUDA
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            info["cuda_device"] = torch.cuda.get_device_name(0)
    except Exception:
        info["torch"] = "not installed"

    # Optional: f5-tts, torchcodec versions
    for pkg in ("f5_tts", "torchcodec", "stable_whisper", "whisper"):
        try:
            mod = __import__(pkg)
            info[pkg] = getattr(mod, "__version__", "unknown")
        except ImportError:
            info[pkg] = "not installed"
        except Exception as e:
            info[pkg] = f"error: {e}"

    try:
        with open(os.path.join(SESSION_DIR, "system_info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_event("WARN", f"Could not write system_info.json: {e}")


def log_event(level: str, message: str, **context):
    """Ghi 1 event vào master log + session log.

    Args:
        level: "INFO" | "WARN" | "ERROR" | "FATAL"
        message: text mô tả
        **context: extra key-value sẽ append vào dòng log
    """
    if not DEBUG_DIR:
        # Chưa setup, fallback ra stderr
        print(f"[{level}] {message}", file=sys.stderr)
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ctx_str = ""
    if context:
        ctx_str = " | " + " ".join(f"{k}={v!r}" for k, v in context.items())
    line = f"[{timestamp}] [{level}] {message}{ctx_str}\n"

    # Append vào master log + session log
    for path in (MASTER_LOG, os.path.join(SESSION_DIR or "", "errors.log")):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def log_exception(message: str, exc: Exception = None, **context):
    """Ghi exception với full traceback. Nếu exc=None, tự lấy từ sys.exc_info()."""
    if exc is None:
        tb_text = traceback.format_exc()
    else:
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    log_event("ERROR", message, **context)
    if not DEBUG_DIR:
        print(tb_text, file=sys.stderr)
        return

    line = f"--- Traceback for: {message} ---\n{tb_text}\n"
    for path in (MASTER_LOG, os.path.join(SESSION_DIR or "", "errors.log")):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


def save_render_failure(operation: str, command: list = None,
                         stderr_text: str = "", exception: Exception = None,
                         extra_context: dict = None) -> str:
    """Lưu 1 file debug riêng cho lần render fail.

    Returns: path tới file debug để hiển thị cho user.
    """
    if not SESSION_DIR:
        return ""

    timestamp = datetime.now().strftime("%H%M%S")
    safe_op = "".join(c if c.isalnum() or c in "_-" else "_" for c in operation)[:40]
    fname = f"render_fail_{safe_op}_{timestamp}.txt"
    fpath = os.path.join(SESSION_DIR, fname)

    lines = []
    lines.append("=" * 70)
    lines.append(f"RENDER FAILURE DEBUG REPORT")
    lines.append("=" * 70)
    lines.append(f"Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Thao tác: {operation}")
    lines.append("")

    if extra_context:
        lines.append("--- CONTEXT ---")
        for k, v in extra_context.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    if command:
        lines.append("--- FFMPEG COMMAND ---")
        # Format command đẹp, mỗi arg 1 dòng nếu dài
        cmd_str = " ".join(f'"{a}"' if " " in str(a) else str(a) for a in command)
        lines.append(cmd_str)
        lines.append("")
        # Cũng format multi-line dễ đọc
        lines.append("--- COMMAND BREAKDOWN ---")
        for arg in command:
            lines.append(f"  {arg}")
        lines.append("")

    if stderr_text:
        lines.append("--- FFMPEG STDERR (last 5000 chars) ---")
        lines.append(stderr_text[-5000:] if len(stderr_text) > 5000 else stderr_text)
        lines.append("")

    if exception:
        lines.append("--- PYTHON EXCEPTION ---")
        tb_text = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        ))
        lines.append(tb_text)
        lines.append("")

    lines.append("=" * 70)
    lines.append("HƯỚNG DẪN: Gửi file này (và toàn bộ folder session_*) cho dev.")
    lines.append(f"Để xuất bundle nén: dùng nút 'Xuất debug bundle' trong app.")
    lines.append("=" * 70)

    try:
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log_event("ERROR", f"Render failure saved: {fname}", operation=operation)
        return fpath
    except Exception as e:
        log_event("WARN", f"Could not save render_fail file: {e}")
        return ""


def export_debug_bundle(target_path: str = None) -> str:
    """Nén toàn bộ session hiện tại + master log thành 1 zip.

    Args:
        target_path: nơi lưu zip; nếu None thì lưu trong DEBUG_DIR.

    Returns: path tới zip file, hoặc "" nếu fail.
    """
    if not DEBUG_DIR:
        return ""

    if target_path is None:
        target_path = os.path.join(
            DEBUG_DIR, f"debug_bundle_{_session_start_iso}.zip"
        )

    try:
        with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Master log
            if os.path.exists(MASTER_LOG):
                zf.write(MASTER_LOG, "app_errors.log")
            # Toàn bộ session folder
            if SESSION_DIR and os.path.exists(SESSION_DIR):
                base = os.path.basename(SESSION_DIR)
                for root, _, files in os.walk(SESSION_DIR):
                    for fn in files:
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, os.path.dirname(SESSION_DIR))
                        zf.write(full, rel)
        log_event("INFO", f"Debug bundle exported: {target_path}")
        return target_path
    except Exception as e:
        log_event("ERROR", f"Could not create bundle: {e}")
        return ""


def get_session_dir() -> str:
    """Trả về path folder session hiện tại."""
    return SESSION_DIR or ""


def get_recent_errors(n: int = 20) -> list:
    """Đọc N dòng log cuối từ session errors.log."""
    if not SESSION_DIR:
        return []
    log_path = os.path.join(SESSION_DIR, "errors.log")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return lines[-n:]
    except Exception:
        return []
