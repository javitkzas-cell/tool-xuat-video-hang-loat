"""layout_composer.py — Trình kéo-thả bố cục cho chế độ xuất video bằng ảnh (Ken Burns).

Một canvas tkinter tái sử dụng được cho:
  - Panel phải (nhỏ, nhúng trong giao diện chính)
  - Popup mở rộng (lớn)

Mô hình dữ liệu (chia sẻ trên app, normalize theo khung 0..1 của video W×H):
  app.overlay_layers : list[dict] — các thành phần user thêm vào
      {
        'id': 'L1', 'type': 'image'|'greenscreen', 'name': 'Thành phần 1',
        'path': '<file gốc>',
        'x','y','w','h': float,            # vị trí góc trên-trái + kích thước (phân số)
        'key_method': 'none'|'auto'|'rembg'|'grabcut'|'color',
        'key_erode': int, 'key_feather': float,
        'chroma_color': '0x00D800', 'chroma_sim': 0.20, 'chroma_blend': 0.10,
        'opacity': float (0..1),
      }
  app.kb_text_layout : dict — ô phụ đề kéo-thả/resize
      {'x','y','w','h': float, 'enabled': bool}

Composer chỉ lo phần GUI + thao tác. Các việc nặng (tách nền, lấy frame video)
delegate về app: app._prepare_jesus_image(...) và app._grab_video_frame(...).
"""

import os
import tkinter as tk
import tkinter.font as tkfont

# Phiên bản composer — app.py kiểm tra lúc khởi động để phát hiện
# trường hợp máy chỉ được copy đè app.py mà quên layout_composer.py
COMPOSER_VERSION = "2.0"

from PIL import Image, ImageTk

# Các phương pháp tách nền (label hiển thị ↔ mã nội bộ)
KEY_METHODS = {
    "Tắt (giữ nguyên)": "none",
    "Tự động (AI→vùng→màu)": "auto",
    "AI thông minh (rembg)": "rembg",
    "Theo vùng (GrabCut)": "grabcut",
    "Theo màu (phông xanh/đặc)": "color",
}
KEY_METHODS_INV = {v: k for k, v in KEY_METHODS.items()}

_VALID_IMG = ('.png', '.jpg', '.jpeg', '.webp', '.bmp')
_VALID_VID = ('.mp4', '.mov', '.avi', '.mkv', '.webm')

SAMPLE_SUBTITLE = "THE LORD IS GUIDING YOUR BREAKTHROUGH TODAY"
HANDLE = 9  # nửa cạnh ô handle resize (px) — to cho dễ bắt


class LayoutComposer:
    def __init__(self, app, parent, cw, ch, show_panel=False,
                 on_expand=None, on_render_frame=None, on_change=None):
        self.app = app
        self.parent = parent
        self.cw = int(cw)
        self.ch = int(ch)
        self.on_expand = on_expand
        self.on_render_frame = on_render_frame
        self.on_change = on_change

        # Tỉ lệ khung video (mặc định 16:9) — dùng để giữ aspect ảnh
        self.frame_w, self.frame_h = 1920, 1080
        self.frame_aspect = self.frame_w / self.frame_h

        self.sel = None          # id layer đang chọn, hoặc 'text', hoặc None
        self.refs = {}           # giữ tham chiếu ImageTk (tránh GC)
        self._thumb_cache = {}   # signature -> PIL RGBA (ảnh đã tách nền)
        self._drag = {'mode': None, 'ox': 0, 'oy': 0}
        self._last_click = (-999, -999)   # cho click-xoay-vòng khi đè nhau
        self._bg_src = None
        self._sub_pil = None     # lớp phụ đề ASS trong suốt (live preview đẩy vào)

        self._build()

    # ============================================================ BUILD UI
    def _build(self):
        import customtkinter as ctk
        self.ctk = ctk

        self.outer = ctk.CTkFrame(self.parent, fg_color="transparent")
        self.outer.pack(fill="both", expand=True)

        # --- Thanh công cụ ---
        bar = ctk.CTkFrame(self.outer, fg_color="transparent")
        bar.pack(fill="x", padx=2, pady=(0, 3))

        def _btn(txt, cmd, color, w=0):
            b = ctk.CTkButton(bar, text=txt, command=cmd, height=28,
                              fg_color=color, font=ctk.CTkFont(size=12))
            if w:
                b.configure(width=w)
            b.pack(side="left", padx=2)
            return b

        _btn("➕ Thêm thành phần", self.add_layer, "#2980b9")
        _btn("🎵 Sóng radio", self.add_waveform, "#16a085")
        self.btn_tune = _btn("⚙ Tinh chỉnh", self.open_tune_dialog, "#8e44ad")
        _btn("🗑", self.delete_selected, "#7f8c8d", w=36)
        if self.on_render_frame:
            _btn("🎬 Frame thật", self.on_render_frame, "#16a085")
        if self.on_expand:
            _btn("⛶ Mở rộng", self.on_expand, "#d35400")

        # --- Canvas ---
        self.canvas = tk.Canvas(self.outer, width=self.cw, height=self.ch,
                                bg="#0b0b0f", highlightthickness=1,
                                highlightbackground="#333333")
        self.canvas.pack(padx=0, pady=0)

        self.canvas.bind('<Button-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', lambda e: self.open_tune_dialog())
        self.canvas.bind('<Delete>', lambda e: self.delete_selected())
        for key, dx, dy in (('<Left>', -1, 0), ('<Right>', 1, 0),
                            ('<Up>', 0, -1), ('<Down>', 0, 1)):
            self.canvas.bind(key, lambda e, dx=dx, dy=dy: self._nudge(dx, dy))

        # --- Danh sách thành phần (chip chọn nhanh, tránh lẫn khi đè nhau) ---
        self.list_bar = ctk.CTkScrollableFrame(self.outer, orientation="horizontal",
                                               height=44, fg_color="#161616")
        self.list_bar.pack(fill="x", padx=2, pady=(4, 0))

        self._hint_lbl = ctk.CTkLabel(
            self.outer,
            text="Mẹo: bấm chip để chọn · kéo để di chuyển · kéo 8 ô vàng quanh viền để phóng to/thu nhỏ · "
                 "bấm lại 1 chỗ để chọn lớp bên dưới · phím mũi tên để nhích · nháy đúp để tinh chỉnh",
            font=ctk.CTkFont(size=10), text_color="gray", wraplength=self.cw, justify="left")
        self._hint_lbl.pack(anchor="w", padx=4, pady=(2, 0))

        self._load_background()
        self._refresh_layer_list()
        self.render_all()

    # ============================================================ NỀN MẪU
    def _load_background(self):
        """Nạp ảnh nền mẫu (cover-fit CW×CH).
        - Mode video nền (jesus_split): 1 frame từ video nền (cụ thể / random).
        - Mode ảnh (ken_burns): 1 ảnh từ thư mục ảnh.
        Không có nguồn → nền đen."""
        import random
        bg = None
        try:
            if getattr(self.app, 'render_mode', 'ken_burns') == 'jesus_split':
                # Nguồn video nền: cụ thể hoặc random từ thư mục
                vid = getattr(self.app, 'bg_video_specific_file', '') or ''
                if not (vid and os.path.isfile(vid)):
                    folder = getattr(self.app, 'bg_video_folder', '') or ''
                    if folder and os.path.isdir(folder):
                        vids = [os.path.join(folder, f) for f in os.listdir(folder)
                                if f.lower().endswith(_VALID_VID)]
                        if vids:
                            if not self._bg_src or self._bg_src not in vids:
                                self._bg_src = random.choice(vids)
                            vid = self._bg_src
                if vid and os.path.isfile(vid):
                    self._bg_src = vid
                    pil = self.app._grab_video_frame(vid)
                    if pil is not None:
                        bg = pil.convert('RGB')
            else:
                folder = getattr(self.app, 'img_folder', '') or ''
                if folder and os.path.isdir(folder):
                    imgs = [os.path.join(folder, f) for f in os.listdir(folder)
                            if f.lower().endswith(_VALID_IMG)]
                    if imgs:
                        if not self._bg_src or self._bg_src not in imgs:
                            self._bg_src = random.choice(imgs)
                        bg = Image.open(self._bg_src).convert('RGB')
        except Exception:
            bg = None
        if bg is None:
            bg = Image.new('RGB', (self.cw, self.ch), (24, 24, 30))
        self._bg_pil = self._cover_fit(bg, self.cw, self.ch)

    @staticmethod
    def _cover_fit(pil, w, h):
        iw, ih = pil.size
        scale = max(w / iw, h / ih)
        nw, nh = max(int(iw * scale), 1), max(int(ih * scale), 1)
        pil = pil.resize((nw, nh), Image.LANCZOS)
        left = (nw - w) // 2
        top = (nh - h) // 2
        return pil.crop((left, top, left + w, top + h))

    # ============================================================ THUMB / KEYING
    def _layer_by_id(self, lid):
        for l in self.app.overlay_layers:
            if l['id'] == lid:
                return l
        return None

    def _get_thumb(self, layer):
        cw_px = max(8, int(layer['w'] * self.cw))
        ch_px = max(8, int(layer['h'] * self.ch))
        if layer.get('type') == 'waveform':
            return self._wave_sample_pil(layer, cw_px, ch_px)
        sig = (layer['id'], layer.get('path', ''), layer.get('key_method', 'none'),
               layer.get('key_erode', 0), layer.get('key_feather', 1.0))
        base = self._thumb_cache.get(sig)
        if base is None:
            base = self._build_keyed_pil(layer)
            self._thumb_cache[sig] = base
        try:
            return base.resize((cw_px, ch_px), Image.LANCZOS)
        except Exception:
            return base.resize((cw_px, ch_px))

    def _build_keyed_pil(self, layer):
        try:
            src = layer.get('path', '')
            if layer['type'] == 'greenscreen':
                pil = self.app._grab_video_frame(src)
                if pil is None:
                    return self._placeholder(layer)
                # Chưa bật tách nền → hiện frame nguyên bản
                if layer.get('key_method', 'none') != 'color':
                    return pil.convert('RGBA')
                tmp = os.path.join(self._tmp(), f"_gs_frame_{layer['id']}.png")
                pil.convert('RGB').save(tmp)
                keyed, _ = self.app._prepare_jesus_image(
                    tmp, method='color',
                    erode=layer.get('key_erode', 0),
                    feather=layer.get('key_feather', 1.0))
                return Image.open(keyed).convert('RGBA')
            else:
                method = layer.get('key_method', 'none')
                if method == 'none':
                    return Image.open(src).convert('RGBA')
                keyed, _ = self.app._prepare_jesus_image(
                    src, method=method,
                    erode=layer.get('key_erode', 0),
                    feather=layer.get('key_feather', 1.0))
                return Image.open(keyed).convert('RGBA')
        except Exception as e:
            try:
                self.app.log(f"⚠️ Composer: lỗi nạp '{layer.get('name')}' ({e})")
            except Exception:
                pass
            return self._placeholder(layer)

    def _placeholder(self, layer):
        from PIL import ImageDraw
        ph = Image.new('RGBA', (200, 200), (90, 90, 110, 200))
        d = ImageDraw.Draw(ph)
        d.rectangle([2, 2, 197, 197], outline=(255, 255, 255, 230), width=3)
        d.text((16, 90), layer.get('name', '?'), fill=(255, 255, 255, 255))
        return ph

    def _tmp(self):
        t = getattr(self.app, 'TEMP_DIR', None)
        if t and os.path.isdir(t):
            return t
        import tempfile
        return tempfile.gettempdir()

    def _invalidate_thumb(self, layer):
        for k in list(self._thumb_cache.keys()):
            if k and k[0] == layer['id']:
                del self._thumb_cache[k]

    # ============================================================ RESIZE
    def resize(self, cw, ch):
        """Đổi kích thước canvas (giữ 16:9) khi vùng preview co giãn."""
        cw, ch = int(cw), int(ch)
        if cw == self.cw and ch == self.ch:
            return
        self.cw, self.ch = cw, ch
        try:
            self.canvas.config(width=cw, height=ch)
            self._hint_lbl.configure(wraplength=cw)
        except Exception:
            return
        # Resize tạm nền/phụ đề hiện có cho đỡ giật — frame live kế tiếp sẽ thay
        try:
            self._bg_pil = self._bg_pil.resize((cw, ch), Image.BILINEAR)
        except Exception:
            pass
        if self._sub_pil is not None:
            try:
                self._sub_pil = self._sub_pil.resize((cw, ch), Image.BILINEAR)
            except Exception:
                self._sub_pil = None
        self.render_all()

    # ============================================================ LIVE PREVIEW
    def set_live_frame(self, bg_pil, sub_pil=None):
        """Live preview đẩy frame video (đã zoom/grade) + lớp phụ đề ASS vào canvas.
        CHỈ cập nhật ảnh nền & phụ đề — KHÔNG vẽ lại layer (giữ mượt khi kéo-thả)."""
        try:
            if bg_pil.size != (self.cw, self.ch):
                bg_pil = bg_pil.resize((self.cw, self.ch), Image.BILINEAR)
            self._bg_pil = bg_pil.convert('RGB')
            if sub_pil is not None and sub_pil.size != (self.cw, self.ch):
                sub_pil = sub_pil.resize((self.cw, self.ch), Image.BILINEAR)
            self._sub_pil = sub_pil

            c = self.canvas
            self.refs['bg'] = ImageTk.PhotoImage(self._bg_pil)
            bg_items = c.find_withtag('bgimg')
            if bg_items:
                c.itemconfig(bg_items[0], image=self.refs['bg'])
            else:
                self.render_all()
                return
            # Lớp phụ đề: nằm TRÊN layer (giống render thật), dưới khung chọn
            if self._sub_pil is not None:
                self.refs['subov'] = ImageTk.PhotoImage(self._sub_pil)
                s_items = c.find_withtag('subov')
                if s_items:
                    c.itemconfig(s_items[0], image=self.refs['subov'])
                else:
                    c.create_image(0, 0, anchor='nw', image=self.refs['subov'],
                                   tags=('subov',))
                c.tag_raise('subov')
                c.tag_raise('textbg'); c.tag_raise('text')   # khung kéo-thả vẫn thao tác được
        except Exception:
            pass

    # ============================================================ RENDER CANVAS
    def render_all(self):
        c = self.canvas
        c.delete('all')
        self.refs.clear()

        self.refs['bg'] = ImageTk.PhotoImage(self._bg_pil)
        c.create_image(0, 0, anchor='nw', image=self.refs['bg'], tags=('bgimg',))

        for layer in self.app.overlay_layers:
            self._draw_layer(layer)

        # Lớp phụ đề ASS live (nếu có) — trên layer, dưới khung chọn/kéo-thả
        if self._sub_pil is not None:
            self.refs['subov'] = ImageTk.PhotoImage(self._sub_pil)
            c.create_image(0, 0, anchor='nw', image=self.refs['subov'], tags=('subov',))

        self._draw_text_box()
        self._draw_selection()

    def _draw_layer(self, layer):
        c = self.canvas
        try:
            op = layer.get('opacity', 1.0)
            pil = self._get_thumb(layer)
            if op < 0.99:
                a = pil.split()[3].point(lambda v: int(v * op))
                pil.putalpha(a)
            tkimg = ImageTk.PhotoImage(pil)
            self.refs[layer['id']] = tkimg
            x = layer['x'] * self.cw
            y = layer['y'] * self.ch
            c.create_image(x, y, anchor='nw', image=tkimg, tags=('layer', layer['id']))
        except Exception:
            pass

    def _draw_text_box(self):
        c = self.canvas
        tl = self.app.kb_text_layout
        x = tl['x'] * self.cw
        y = tl['y'] * self.ch
        w = tl['w'] * self.cw
        h = tl['h'] * self.ch
        # KHÔNG vẽ chữ mẫu — chữ chỉ hiện khi app đính kèm kịch bản (lớp ASS live).
        # Ở đây chỉ vẽ khung kéo-thả của ô phụ đề.
        outline = '#f1c40f' if self.sel == 'text' else '#3498db'
        c.create_rectangle(x, y, x + w, y + h, outline=outline, width=2,
                          dash=(5, 3), tags=('text',))
        if self.sel == 'text':
            self._draw_handles(x, y, w, h)

    def _auto_text_px(self):
        try:
            fs = int(self.app.slider_fontsize.get())
        except Exception:
            fs = 90
        return max(9, int(fs * (self.ch / self.frame_h)))

    def _draw_selection(self):
        if not self.sel or self.sel == 'text':
            return
        layer = self._layer_by_id(self.sel)
        if not layer:
            return
        c = self.canvas
        x = layer['x'] * self.cw
        y = layer['y'] * self.ch
        w = layer['w'] * self.cw
        h = layer['h'] * self.ch
        c.create_rectangle(x, y, x + w, y + h, outline='#f1c40f', width=2, dash=(4, 3))
        self._draw_handles(x, y, w, h)

    @staticmethod
    def _handle_points(x, y, w, h):
        """8 vị trí handle: 4 góc + 4 trung điểm cạnh. Trả về dict code→(hx,hy).
        Code: n/s = trên/dưới, w/e = trái/phải; góc = kết hợp (vd 'ne')."""
        return {
            'nw': (x,         y),
            'n':  (x + w / 2, y),
            'ne': (x + w,     y),
            'e':  (x + w,     y + h / 2),
            'se': (x + w,     y + h),
            's':  (x + w / 2, y + h),
            'sw': (x,         y + h),
            'w':  (x,         y + h / 2),
        }

    def _draw_handles(self, x, y, w, h):
        for hx, hy in self._handle_points(x, y, w, h).values():
            self.canvas.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE,
                                        fill='#f1c40f', outline='white', width=2)

    # ============================================================ HIT-TEST
    def _layers_at(self, ex, ey):
        """Danh sách id layer chứa điểm (ex,ey), TRÊN xuống dưới."""
        fx, fy = ex / self.cw, ey / self.ch
        hits = []
        for layer in reversed(self.app.overlay_layers):   # cuối list = trên cùng
            if (layer['x'] <= fx <= layer['x'] + layer['w'] and
                    layer['y'] <= fy <= layer['y'] + layer['h']):
                hits.append(layer['id'])
        return hits

    def _text_at(self, ex, ey):
        tl = self.app.kb_text_layout
        fx, fy = ex / self.cw, ey / self.ch
        return tl['x'] <= fx <= tl['x'] + tl['w'] and tl['y'] <= fy <= tl['y'] + tl['h']

    def _hit_sel_handle(self, ex, ey):
        """Trả về mã handle ('nw','n','ne','e','se','s','sw','w') nếu bấm trúng
        1 trong 8 ô vàng của vật đang chọn, ngược lại None. Ưu tiên GÓC trước
        CẠNH (vật nhỏ thì các ô có thể chồng lên nhau)."""
        if not self.sel:
            return None
        if self.sel == 'text':
            tl = self.app.kb_text_layout
            x, y, w, h = (tl['x'] * self.cw, tl['y'] * self.ch,
                          tl['w'] * self.cw, tl['h'] * self.ch)
        else:
            layer = self._layer_by_id(self.sel)
            if not layer:
                return None
            x, y, w, h = (layer['x'] * self.cw, layer['y'] * self.ch,
                          layer['w'] * self.cw, layer['h'] * self.ch)
        pts = self._handle_points(x, y, w, h)
        for code in ('nw', 'ne', 'se', 'sw', 'n', 'e', 's', 'w'):
            hx, hy = pts[code]
            if abs(ex - hx) <= HANDLE + 2 and abs(ey - hy) <= HANDLE + 2:
                return code
        return None

    # ============================================================ EVENTS
    def _on_press(self, e):
        self.canvas.focus_set()

        # 1) Handle resize của vật ĐANG chọn — ưu tiên cao nhất
        _hcode = self._hit_sel_handle(e.x, e.y)
        if _hcode:
            self._drag['mode'] = 'resize_text' if self.sel == 'text' else 'resize'
            self._drag['hcode'] = _hcode
            # Lưu khung GỐC lúc bấm — mọi tính toán kéo dựa trên khung này
            if self.sel == 'text':
                tl = self.app.kb_text_layout
                self._drag['rect0'] = (tl['x'], tl['y'], tl['w'], tl['h'])
            else:
                _l = self._layer_by_id(self.sel)
                self._drag['rect0'] = (_l['x'], _l['y'], _l['w'], _l['h'])
            self._last_click = (e.x, e.y)
            return

        same_spot = (abs(e.x - self._last_click[0]) <= 4 and
                     abs(e.y - self._last_click[1]) <= 4)
        self._last_click = (e.x, e.y)

        text_hit = self._text_at(e.x, e.y)

        # ƯU TIÊN vật ĐANG chọn: nếu bấm trong vùng của nó → kéo nó luôn,
        # không để layer/ô khác bên dưới cướp mất (kể cả khi bị đè rộng).
        if self.sel == 'text' and text_hit:
            tl = self.app.kb_text_layout
            self._drag['mode'] = 'move_text'
            self._drag['ox'] = e.x - tl['x'] * self.cw
            self._drag['oy'] = e.y - tl['y'] * self.ch
            self.render_all()
            return

        hits = self._layers_at(e.x, e.y)
        if hits:
            # Click-xoay-vòng: bấm lại đúng chỗ → chọn lớp bên dưới
            if same_spot and self.sel in hits and len(hits) > 1:
                i = hits.index(self.sel)
                self.sel = hits[(i + 1) % len(hits)]
            elif self.sel in hits:
                pass  # giữ vật đang chọn để kéo (kể cả khi bị đè)
            else:
                self.sel = hits[0]
            layer = self._layer_by_id(self.sel)
            self._drag['mode'] = 'move'
            self._drag['ox'] = e.x - layer['x'] * self.cw
            self._drag['oy'] = e.y - layer['y'] * self.ch
        elif text_hit:
            self.sel = 'text'
            tl = self.app.kb_text_layout
            self._drag['mode'] = 'move_text'
            self._drag['ox'] = e.x - tl['x'] * self.cw
            self._drag['oy'] = e.y - tl['y'] * self.ch
        else:
            self.sel = None
            self._drag['mode'] = None

        self._refresh_layer_list()
        self.render_all()

    def _on_motion(self, e):
        m = self._drag['mode']
        if m is None:
            return
        if m == 'move':
            layer = self._layer_by_id(self.sel)
            if not layer:
                return
            layer['x'] = self._clamp((e.x - self._drag['ox']) / self.cw, -0.5, 1.0)
            layer['y'] = self._clamp((e.y - self._drag['oy']) / self.ch, -0.5, 1.0)
        elif m == 'resize':
            layer = self._layer_by_id(self.sel)
            if not layer:
                return
            code = self._drag.get('hcode', 'se')
            x0, y0, w0, h0 = self._drag.get('rect0', (layer['x'], layer['y'], layer['w'], layer['h']))
            fx, fy = e.x / self.cw, e.y / self.ch
            MIN, MAX = 0.03, 1.6
            nx, ny, nw, nh = x0, y0, w0, h0
            if 'e' in code:
                nw = self._clamp(fx - x0, MIN, MAX)
            if 's' in code:
                nh = self._clamp(fy - y0, MIN, MAX)
            if 'w' in code:
                _nx = self._clamp(fx, x0 + w0 - MAX, x0 + w0 - MIN)
                nw = x0 + w0 - _nx
                nx = _nx
            if 'n' in code:
                _ny = self._clamp(fy, y0 + h0 - MAX, y0 + h0 - MIN)
                nh = y0 + h0 - _ny
                ny = _ny
            asp = layer.get('_aspect')
            if asp:
                # Giữ tỉ lệ ảnh: chiều kéo chính quyết định, chiều kia suy ra
                if code in ('n', 's'):
                    nw = nh * asp / self.frame_aspect
                    nx = x0 + (w0 - nw) / 2          # giữ tâm ngang
                elif code in ('e', 'w'):
                    nh = nw * self.frame_aspect / asp
                    ny = y0 + (h0 - nh) / 2          # giữ tâm dọc
                else:                                 # 4 góc: theo chiều ngang
                    nh = nw * self.frame_aspect / asp
                    if 'n' in code:
                        ny = y0 + h0 - nh             # giữ mép dưới cố định
            layer['x'], layer['y'], layer['w'], layer['h'] = nx, ny, nw, nh
        elif m == 'move_text':
            tl = self.app.kb_text_layout
            tl['x'] = self._clamp((e.x - self._drag['ox']) / self.cw, 0.0, 1.0 - tl['w'])
            tl['y'] = self._clamp((e.y - self._drag['oy']) / self.ch, 0.0, 1.0 - tl['h'])
            tl['enabled'] = True
        elif m == 'resize_text':
            tl = self.app.kb_text_layout
            code = self._drag.get('hcode', 'se')
            x0, y0, w0, h0 = self._drag.get('rect0', (tl['x'], tl['y'], tl['w'], tl['h']))
            fx, fy = e.x / self.cw, e.y / self.ch
            if 'e' in code:
                tl['w'] = self._clamp(fx - x0, 0.1, 1.0 - x0)
            if 's' in code:
                tl['h'] = self._clamp(fy - y0, 0.05, 1.0 - y0)
            if 'w' in code:
                _nx = self._clamp(fx, 0.0, x0 + w0 - 0.1)
                tl['w'] = x0 + w0 - _nx
                tl['x'] = _nx
            if 'n' in code:
                _ny = self._clamp(fy, 0.0, y0 + h0 - 0.05)
                tl['h'] = y0 + h0 - _ny
                tl['y'] = _ny
            tl['enabled'] = True
        self.render_all()

    def _on_release(self, e):
        if self._drag['mode']:
            self._drag['mode'] = None
            self._notify_change()

    def _nudge(self, dx, dy):
        step = 0.005
        if self.sel == 'text':
            tl = self.app.kb_text_layout
            tl['x'] = self._clamp(tl['x'] + dx * step, 0.0, 1.0 - tl['w'])
            tl['y'] = self._clamp(tl['y'] + dy * step, 0.0, 1.0 - tl['h'])
            tl['enabled'] = True
            self.render_all()
            self._notify_change()
        elif self.sel:
            layer = self._layer_by_id(self.sel)
            if layer:
                layer['x'] = self._clamp(layer['x'] + dx * step, -0.5, 1.0)
                layer['y'] = self._clamp(layer['y'] + dy * step, -0.5, 1.0)
                self.render_all()
                self._notify_change()

    @staticmethod
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    # ============================================================ DANH SÁCH CHIP
    def _refresh_layer_list(self):
        ctk = self.ctk
        for w in self.list_bar.winfo_children():
            w.destroy()

        # Chip ô phụ đề (luôn có)
        self._make_chip("📝 Phụ đề", 'text', '#2c3e50')
        # Chip từng layer
        for i, layer in enumerate(self.app.overlay_layers):
            _t = layer.get('type')
            icon = "🎵" if _t == 'waveform' else ("🟩" if _t == 'greenscreen' else "🖼")
            self._make_chip(f"{icon} {i+1}. {layer['name']}", layer['id'], '#34495e')

        if not self.app.overlay_layers:
            ctk.CTkLabel(self.list_bar, text="(chưa có thành phần — bấm ➕ Thêm thành phần)",
                         font=ctk.CTkFont(size=11), text_color="gray").pack(side="left", padx=6)

    def _make_chip(self, text, sel_id, base_color):
        ctk = self.ctk
        active = (self.sel == sel_id)
        chip = ctk.CTkButton(
            self.list_bar, text=text, height=30,
            fg_color=("#f1c40f" if active else base_color),
            text_color=("black" if active else "white"),
            hover_color="#f39c12",
            font=ctk.CTkFont(size=11, weight="bold" if active else "normal"),
            command=lambda s=sel_id: self._select_chip(s))
        chip.pack(side="left", padx=3, pady=4)

    def _select_chip(self, sel_id):
        self.sel = sel_id
        self._refresh_layer_list()
        self.render_all()

    # ============================================================ THÊM / XOÁ / TINH CHỈNH
    def add_layer(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Thêm thành phần (ảnh hoặc video)",
            filetypes=[
                ("Ảnh hoặc Video", "*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.avi *.mkv *.webm"),
                ("Ảnh", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("Video", "*.mp4 *.mov *.avi *.mkv *.webm"),
            ])
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        is_video = ext in _VALID_VID
        ltype = 'greenscreen' if is_video else 'image'

        self.app._layer_counter = getattr(self.app, '_layer_counter', 0) + 1
        cnt = self.app._layer_counter
        lid = f"L{cnt}"

        # Aspect để giữ tỉ lệ
        aspect = None
        try:
            pil = self.app._grab_video_frame(path) if is_video else Image.open(path)
            if pil:
                aspect = pil.width / max(pil.height, 1)
        except Exception:
            aspect = None

        # Kích thước + vị trí so le (tránh chồng khít lên nhau)
        w = 0.30 if is_video else 0.22
        h = (w * self.frame_aspect / aspect) if aspect else w
        stagger = (cnt % 6) * 0.05
        x = self._clamp(0.06 + stagger, 0.0, max(0.0, 1.0 - w))
        y = self._clamp(0.06 + stagger, 0.0, max(0.0, 1.0 - h))

        layer = {
            'id': lid, 'type': ltype, 'name': f"Thành phần {cnt}", 'path': path,
            'x': x, 'y': y, 'w': w, 'h': h, '_aspect': aspect,
            # KHÔNG tự tách nền khi thêm — cần thì bật trong ⚙ Tinh chỉnh
            'key_method': 'none',
            'key_erode': 0, 'key_feather': 1.0,
            'chroma_color': '0x00D800', 'chroma_sim': 0.20, 'chroma_blend': 0.10,
            'opacity': 1.0,
        }
        self.app.overlay_layers.append(layer)
        self.sel = lid
        self._refresh_layer_list()
        self.render_all()
        self._notify_change()
        try:
            kind = "video" if is_video else "ảnh"
            self.app.log(f"➕ Đã thêm thành phần {kind}: {os.path.basename(path)} "
                         f"(giữ nguyên — cần tách nền thì bật trong ⚙ Tinh chỉnh)")
        except Exception:
            pass

    def add_waveform(self):
        """Thêm 1 dải sóng radio (showwaves) — chạy theo giọng đọc khi render."""
        self.app._layer_counter = getattr(self.app, '_layer_counter', 0) + 1
        cnt = self.app._layer_counter
        lid = f"L{cnt}"
        layer = {
            'id': lid, 'type': 'waveform', 'name': "Sóng radio", 'path': '',
            'x': 0.10, 'y': 0.74, 'w': 0.80, 'h': 0.16, '_aspect': None,
            'wave_color': '0x66E0FF', 'wave_hex': '#66E0FF', 'opacity': 1.0,
        }
        self.app.overlay_layers.append(layer)
        self.sel = lid
        self._refresh_layer_list()
        self.render_all()
        self._notify_change()
        try:
            self.app.log("🎵 Đã thêm Sóng radio (chạy theo giọng đọc khi render).")
        except Exception:
            pass

    @staticmethod
    def _hex_to_rgb(hx):
        hx = (hx or '#66E0FF').lstrip('#')
        try:
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return (102, 224, 255)

    def _wave_sample_pil(self, layer, w, h):
        """Ảnh sóng MẪU (tĩnh) cho editor — chỉ minh hoạ vị trí/kích thước/màu.
        Sóng THẬT được FFmpeg vẽ theo giọng đọc lúc render."""
        import math
        from PIL import ImageDraw
        rgb = self._hex_to_rgb(layer.get('wave_hex', '#66E0FF'))
        img = Image.new('RGBA', (max(w, 2), max(h, 2)), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cy = h / 2.0
        for x in range(w):
            env = (0.45 + 0.55 * abs(math.sin(x * 0.045))) * (0.55 + 0.45 * math.sin(x * 0.21 + 1.3))
            amp = max(1.0, env * (h * 0.46))
            d.line([(x, cy - amp), (x, cy + amp)], fill=rgb + (255,), width=1)
        return img

    def delete_selected(self):
        if not self.sel or self.sel == 'text':
            return
        self.app.overlay_layers = [l for l in self.app.overlay_layers if l['id'] != self.sel]
        self.sel = None
        self._refresh_layer_list()
        self.render_all()
        self._notify_change()

    def _sel_layer(self):
        if not self.sel or self.sel == 'text':
            return None
        return self._layer_by_id(self.sel)

    def open_tune_dialog(self):
        """Popup tinh chỉnh tách nền / độ rõ cho thành phần đang chọn."""
        layer = self._sel_layer()
        if not layer:
            try:
                self.app.log("ℹ️ Hãy chọn 1 thành phần (bấm chip hoặc bấm vào nó) rồi Tinh chỉnh.")
            except Exception:
                pass
            return
        ctk = self.ctk
        win = ctk.CTkToplevel(self.app)
        win.title(f"⚙ Tinh chỉnh — {layer['name']}")
        win.geometry("380x460")
        win.transient(self.app)
        try:
            win.grab_set()
        except Exception:
            pass

        # ---- Nhánh SÓNG RADIO: chỉ chỉnh màu (HEX) + độ rõ ----
        if layer.get('type') == 'waveform':
            win.geometry("360x300")
            ctk.CTkLabel(win, text="🎵 Sóng radio — chạy theo giọng đọc khi render",
                         font=ctk.CTkFont(size=12, weight="bold"), wraplength=330,
                         justify="left").pack(pady=(12, 6), padx=14, anchor="w")
            ctk.CTkLabel(win, text="Màu sóng (mã HEX, vd #66E0FF):").pack(anchor="w", padx=14, pady=(6, 0))
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=14)
            hex_entry = ctk.CTkEntry(row, placeholder_text="#66E0FF")
            hex_entry.insert(0, layer.get('wave_hex', '#66E0FF'))
            hex_entry.pack(side="left", fill="x", expand=True)
            swatch = ctk.CTkLabel(row, text="  ", width=34,
                                  fg_color=layer.get('wave_hex', '#66E0FF'), corner_radius=4)
            swatch.pack(side="left", padx=(6, 0))

            def apply_color():
                hx = hex_entry.get().strip()
                if not hx.startswith('#'):
                    hx = '#' + hx
                if len(hx) != 7:
                    self.app.log("⚠️ Mã màu không hợp lệ (vd #66E0FF).")
                    return
                layer['wave_hex'] = hx
                layer['wave_color'] = '0x' + hx.lstrip('#').upper()
                try:
                    swatch.configure(fg_color=hx)
                except Exception:
                    pass
                self.render_all()
                self._notify_change()
            ctk.CTkButton(win, text="🎨 Áp dụng màu", command=apply_color,
                          fg_color="#16a085").pack(fill="x", padx=14, pady=(6, 4))

            wop_lbl = ctk.CTkLabel(win, text=f"Độ rõ (opacity): {int(layer.get('opacity',1.0)*100)}%")
            wop_lbl.pack(anchor="w", padx=14, pady=(6, 0))

            def on_wop(v):
                layer['opacity'] = round(float(v), 2)
                wop_lbl.configure(text=f"Độ rõ (opacity): {int(float(v)*100)}%")
                self.render_all()
            wop = ctk.CTkSlider(win, from_=0.2, to=1.0, command=on_wop)
            wop.set(layer.get('opacity', 1.0)); wop.pack(fill="x", padx=14)

            wrow = ctk.CTkFrame(win, fg_color="transparent")
            wrow.pack(fill="x", padx=14, pady=(16, 10))
            ctk.CTkButton(wrow, text="🗑 Xoá", command=lambda: (self.delete_selected(), win.destroy()),
                          fg_color="#c0392b", width=90).pack(side="left")
            ctk.CTkButton(wrow, text="✅ Xong", command=lambda: (apply_color(), win.destroy()),
                          fg_color="#27ae60").pack(side="right")
            return

        is_gs = (layer['type'] == 'greenscreen')
        ctk.CTkLabel(win, text=("🟩 Video phông xanh" if is_gs else "🖼 Ảnh") +
                     f" — {os.path.basename(layer.get('path',''))}",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     wraplength=350, justify="left").pack(pady=(12, 6), padx=14, anchor="w")

        def rerender():
            self._invalidate_thumb(layer)
            self.render_all()

        if not is_gs:
            ctk.CTkLabel(win, text="Phương pháp tách nền:").pack(anchor="w", padx=14, pady=(6, 0))
            menu = ctk.CTkOptionMenu(
                win, values=list(KEY_METHODS.keys()), fg_color="#8e44ad", button_color="#7d3c98")
            menu.set(KEY_METHODS_INV.get(layer.get('key_method', 'auto'), "Tự động (AI→vùng→màu)"))

            def on_method(choice):
                layer['key_method'] = KEY_METHODS.get(choice, 'auto')
                rerender()
            menu.configure(command=on_method)
            menu.pack(fill="x", padx=14, pady=(0, 4))
        else:
            # Công tắc BẬT/TẮT tách nền cho video (mặc định thêm mới = TẮT)
            sw_key = ctk.CTkSwitch(win, text="Tách nền video (phông xanh/màu đặc)")
            if layer.get('key_method', 'none') == 'color':
                sw_key.select()

            def on_key_toggle():
                layer['key_method'] = 'color' if sw_key.get() else 'none'
                rerender()
                self._notify_change()
            sw_key.configure(command=on_key_toggle)
            sw_key.pack(anchor="w", padx=14, pady=(8, 2))

            sim_lbl = ctk.CTkLabel(win, text=f"Độ nhạy phông xanh (similarity): {layer.get('chroma_sim',0.20):.2f}")
            sim_lbl.pack(anchor="w", padx=14, pady=(6, 0))

            def on_sim(v):
                layer['chroma_sim'] = round(float(v), 2)
                sim_lbl.configure(text=f"Độ nhạy phông xanh (similarity): {float(v):.2f}")
                self._notify_change()
            sim = ctk.CTkSlider(win, from_=0.02, to=0.6, command=on_sim)
            sim.set(layer.get('chroma_sim', 0.20)); sim.pack(fill="x", padx=14)

            blend_lbl = ctk.CTkLabel(win, text=f"Hoà rìa (blend): {layer.get('chroma_blend',0.10):.2f}")
            blend_lbl.pack(anchor="w", padx=14, pady=(8, 0))

            def on_blend(v):
                layer['chroma_blend'] = round(float(v), 2)
                blend_lbl.configure(text=f"Hoà rìa (blend): {float(v):.2f}")
                self._notify_change()
            blend = ctk.CTkSlider(win, from_=0.0, to=0.5, command=on_blend)
            blend.set(layer.get('chroma_blend', 0.10)); blend.pack(fill="x", padx=14)

        # Co rìa + mượt rìa (cả 2 loại — giúp xoá lem nền cho đẹp)
        erode_lbl = ctk.CTkLabel(win, text=f"Co rìa (xoá lem nền): {layer.get('key_erode',0)}px")
        erode_lbl.pack(anchor="w", padx=14, pady=(10, 0))

        def on_erode(v):
            layer['key_erode'] = int(v)
            erode_lbl.configure(text=f"Co rìa (xoá lem nền): {int(v)}px")
            rerender()
        es = ctk.CTkSlider(win, from_=0, to=8, number_of_steps=8, command=on_erode)
        es.set(layer.get('key_erode', 0)); es.pack(fill="x", padx=14)

        feather_lbl = ctk.CTkLabel(win, text=f"Mượt rìa: {layer.get('key_feather',1.0):.1f}")
        feather_lbl.pack(anchor="w", padx=14, pady=(8, 0))

        def on_feather(v):
            layer['key_feather'] = round(float(v), 1)
            feather_lbl.configure(text=f"Mượt rìa: {float(v):.1f}")
            rerender()
        fs = ctk.CTkSlider(win, from_=0, to=5, command=on_feather)
        fs.set(layer.get('key_feather', 1.0)); fs.pack(fill="x", padx=14)

        # Độ rõ (opacity)
        op_lbl = ctk.CTkLabel(win, text=f"Độ rõ (opacity): {int(layer.get('opacity',1.0)*100)}%")
        op_lbl.pack(anchor="w", padx=14, pady=(8, 0))

        def on_op(v):
            layer['opacity'] = round(float(v), 2)
            op_lbl.configure(text=f"Độ rõ (opacity): {int(float(v)*100)}%")
            self.render_all()
        op = ctk.CTkSlider(win, from_=0.1, to=1.0, command=on_op)
        op.set(layer.get('opacity', 1.0)); op.pack(fill="x", padx=14)

        # Nút
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(14, 10))
        ctk.CTkButton(row, text="🔁 Đổi file", command=lambda: self._replace_selected(win),
                      fg_color="#2980b9", width=100).pack(side="left", padx=2)
        ctk.CTkButton(row, text="🗑 Xoá", command=lambda: (self.delete_selected(), win.destroy()),
                      fg_color="#c0392b", width=80).pack(side="left", padx=2)
        ctk.CTkButton(row, text="✅ Xong", command=win.destroy,
                      fg_color="#27ae60").pack(side="right", padx=2)

    def _replace_selected(self, parent_win=None):
        layer = self._sel_layer()
        if not layer:
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Đổi file thành phần",
            filetypes=[
                ("Ảnh hoặc Video", "*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.avi *.mkv *.webm"),
            ])
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        is_video = ext in _VALID_VID
        layer['path'] = path
        layer['type'] = 'greenscreen' if is_video else 'image'
        # Đổi file → về trạng thái KHÔNG tách nền (bật lại trong Tinh chỉnh nếu cần)
        layer['key_method'] = 'none'
        try:
            pil = self.app._grab_video_frame(path) if is_video else Image.open(path)
            if pil:
                layer['_aspect'] = pil.width / max(pil.height, 1)
                layer['h'] = layer['w'] * self.frame_aspect / layer['_aspect']
        except Exception:
            pass
        self._invalidate_thumb(layer)
        self._refresh_layer_list()
        self.render_all()
        self._notify_change()
        if parent_win is not None:
            try:
                parent_win.destroy()
            except Exception:
                pass

    # ============================================================ MISC
    def _notify_change(self):
        if callable(self.on_change):
            try:
                self.on_change()
            except Exception:
                pass

    def reload_background(self):
        self._bg_src = None
        self._load_background()
        self.render_all()

    def destroy(self):
        try:
            self.outer.destroy()
        except Exception:
            pass
