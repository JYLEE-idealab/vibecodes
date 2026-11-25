import os
import sys
import time
import socket
import struct
import threading
import subprocess
import ctypes
from ctypes import wintypes
from fractions import Fraction
import re
import cv2
import numpy as np
import av
import dxcam
import customtkinter as ctk

import display_ctl

# Enable Per-Monitor DPI Awareness for crisp UI & exact 1:1 mouse coordinate mapping
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADB_PATH = os.path.join(BASE_DIR, "bin", "adb.exe")
APK_PATH = os.path.join(BASE_DIR, "TabMonitor.apk")

VIDEO_PORT = 12351
TOUCH_PORT = 12352
TABLET_VIDEO_PORT = 12347
TABLET_TOUCH_PORT = 12348
CTRL_PORT = 12355
SUPPORTED_RESOLUTIONS = [(1920, 1080), (1280, 720)]

# ----------------- Windows Multi-Monitor & Input Setup -----------------
user32 = ctypes.windll.user32

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

class MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MONITORINFOF_PRIMARY = 0x00000001

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]

class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]
    _anonymous_ = ("_union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_union", _INPUT_UNION),
    ]

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

CURSOR_SHAPE = np.array([
    [0, 0],
    [0, 22],
    [6, 17],
    [10, 25],
    [13, 24],
    [9, 16],
    [16, 16],
], dtype=np.int32)

def send_mouse_input(norm_x, norm_y, action_type, monitor_rect):
    v_left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    v_top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    v_width = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    v_height = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))

    mon_left, mon_top, mon_right, mon_bottom = monitor_rect
    mon_w = mon_right - mon_left
    mon_h = mon_bottom - mon_top

    px = mon_left + int(norm_x * mon_w)
    py = mon_top + int(norm_y * mon_h)

    # Move physical cursor position directly first
    user32.SetCursorPos(px, py)

    dx = int((px - v_left) * 65535 / v_width)
    dy = int((py - v_top) * 65535 / v_height)

    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    if action_type == 0:
        flags |= MOUSEEVENTF_LEFTDOWN
    elif action_type == 2:
        flags |= MOUSEEVENTF_LEFTUP

    inp = INPUT()
    inp.type = 0
    inp.mi.dx = dx
    inp.mi.dy = dy
    inp.mi.mouseData = 0
    inp.mi.dwFlags = flags
    inp.mi.time = 0
    inp.mi.dwExtraInfo = 0

    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def get_monitor_rect(output_idx):
    monitors = []
    non_primary = []

    def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
            r = info.rcMonitor
            rect = (r.left, r.top, r.right, r.bottom)
            monitors.append(rect)
            if not (info.dwFlags & MONITORINFOF_PRIMARY):
                non_primary.append(rect)
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(monitor_enum_proc), 0)

    if len(non_primary) > 0:
        for rect in non_primary:
            if (rect[2] - rect[0], rect[3] - rect[1]) in SUPPORTED_RESOLUTIONS:
                return rect
        return non_primary[0]

    if output_idx < len(monitors):
        return monitors[output_idx]
    if len(monitors) > 0:
        return monitors[0]
    return (0, 0, 1920, 1080)

def get_cursor_on_monitor(mon_rect, cur_w, cur_h):
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    left, top, right, bottom = mon_rect
    if left <= pt.x < right and top <= pt.y < bottom:
        mon_w = max(1, right - left)
        mon_h = max(1, bottom - top)
        cx = int((pt.x - left) * cur_w / mon_w)
        cy = int((pt.y - top) * cur_h / mon_h)
        return (cx, cy)
    return None

def run_cmd_hidden(args, timeout=8):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

def free_old_server_ports():
    """Terminate any leftover background python server holding VIDEO_PORT/TOUCH_PORT"""
    my_pid = os.getpid()
    try:
        r = run_cmd_hidden(["netstat", "-ano"])
        for line in r.stdout.splitlines():
            if f":{VIDEO_PORT}" in line or f":{TOUCH_PORT}" in line:
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING":
                    pid = int(parts[4])
                    if pid != my_pid and pid > 0:
                        run_cmd_hidden(["taskkill", "/f", "/pid", str(pid)])
    except Exception:
        pass


# ----------------- Separate Log Window -----------------
class LogWindow(ctk.CTkToplevel):
    def __init__(self, master, log_history, on_close_cb):
        super().__init__(master)
        self.title("TabMonitor Studio — 시스템 로그")
        self.geometry("560x360")
        self.minsize(420, 240)
        self.configure(fg_color="#0B0F17")
        self.on_close_cb = on_close_cb

        # Standard Windows UI font ("Malgun Gothic" / "맑은 고딕") matching standard apps
        font_header = ctk.CTkFont(family="Malgun Gothic", size=14, weight="bold")
        font_std = ctk.CTkFont(family="Malgun Gothic", size=12)
        font_btn = ctk.CTkFont(family="Malgun Gothic", size=12)

        top_bar = ctk.CTkFrame(self, fg_color="#131B2E", corner_radius=10, border_width=1, border_color="#23304C")
        top_bar.pack(fill="x", padx=14, pady=(14, 8))

        ctk.CTkLabel(top_bar, text="📋 실시간 시스템 로그", font=font_header, text_color="#F8FAFC").pack(
            side="left", padx=14, pady=10
        )

        ctk.CTkButton(
            top_bar,
            text="로그 지우기",
            font=font_btn,
            width=86,
            height=28,
            corner_radius=8,
            fg_color="#1E293B",
            hover_color="#334155",
            text_color="#E2E8F0",
            command=self.clear_logs,
        ).pack(side="right", padx=12, pady=8)

        self.textbox = ctk.CTkTextbox(
            self,
            font=font_std,
            fg_color="#111622",
            text_color="#E2E8F0",
            border_width=1,
            border_color="#1E293B",
            corner_radius=10,
            activate_scrollbars=True,
        )
        self.textbox.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        if log_history:
            self.textbox.insert("end", "".join(log_history))
            self.textbox.see("end")

        self.protocol("WM_DELETE_WINDOW", self._handle_close)

    def append_line(self, line):
        if self.winfo_exists():
            self.textbox.insert("end", line)
            self.textbox.see("end")

    def clear_logs(self):
        if self.winfo_exists():
            self.textbox.delete("1.0", "end")

    def _handle_close(self):
        if self.on_close_cb:
            self.on_close_cb()
        self.destroy()


# ----------------- Main GUI Application -----------------
class TabMonitorApp(ctk.CTk):
    def __init__(self, auto_start=False, initial_res=None):
        super().__init__()

        ctk.set_appearance_mode("dark")
        self.title("TabMonitor Studio — Galaxy Tab S7 FE 보조모니터")
        self.geometry("620x490")
        self.minsize(580, 470)
        self.configure(fg_color="#0B0F17")

        # State variables
        self.is_streaming = False
        self.is_starting = False
        self.stop_event = threading.Event()
        self.res_change_event = threading.Event()
        self.target_resolution = (1920, 1080)
        self.current_video_client = None
        self.current_touch_client = None
        self.video_server_sock = None
        self.touch_server_sock = None

        # Log storage & separate window reference
        self.log_history = []
        self.log_window = None

        # Telemetry metrics
        self.live_fps = 0.0
        self.live_Mbps = 0.0
        self.last_touch_text = "대기 중"
        self.tablet_connected = False
        self.tablet_model = "연결 대기 중..."
        self.reverse_configured_serial = None

        # Detect current virtual display resolution on startup
        if initial_res in SUPPORTED_RESOLUTIONS:
            self.target_resolution = initial_res
        else:
            cur_res = display_ctl.get_virtual_display_resolution()
            if cur_res in SUPPORTED_RESOLUTIONS:
                self.target_resolution = cur_res

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Clean up any leftover background server so GUI owns the ports
        threading.Thread(target=free_old_server_ports, daemon=True).start()

        # Ensure background USB watcher daemon is running so even if user closes this GUI later,
        # clicking Start on the tablet will re-open this PC GUI automatically!
        self._ensure_usb_watcher_running()

        # Start Control / IPC listener on port 12355
        self._start_ctrl_listener()

        # Start periodic device & telemetry poller
        self._start_background_poller()

        if auto_start:
            self.after(350, lambda: self._start_streaming(launched_from_tablet=True))

    def _ensure_usb_watcher_running(self):
        watcher_py = os.path.join(BASE_DIR, "usb_watcher.py")
        if os.path.exists(watcher_py):
            try:
                pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(pythonw):
                    pythonw = sys.executable
                subprocess.Popen(
                    [pythonw, watcher_py],
                    cwd=BASE_DIR,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
                )
            except Exception:
                pass

    def _build_ui(self):
        font_title = ctk.CTkFont(family="Malgun Gothic", size=22, weight="bold")
        font_sub = ctk.CTkFont(family="Malgun Gothic", size=12)
        font_card_title = ctk.CTkFont(family="Malgun Gothic", size=12, weight="bold")
        font_card_val = ctk.CTkFont(family="Malgun Gothic", size=15, weight="bold")
        font_section = ctk.CTkFont(family="Malgun Gothic", size=14, weight="bold")
        font_btn = ctk.CTkFont(family="Malgun Gothic", size=16, weight="bold")
        font_top_btn = ctk.CTkFont(family="Malgun Gothic", size=12, weight="bold")

        # 1. Header Frame
        header = ctk.CTkFrame(self, fg_color="#131B2E", corner_radius=16, border_width=1, border_color="#23304C")
        header.pack(fill="x", padx=20, pady=(20, 12))

        header_left = ctk.CTkFrame(header, fg_color="transparent")
        header_left.pack(side="left", padx=20, pady=16)

        ctk.CTkLabel(header_left, text="TabMonitor Studio", font=font_title, text_color="#F8FAFC").pack(anchor="w")
        ctk.CTkLabel(
            header_left,
            text="Galaxy Tab S7 FE • USB 초저지연 NVENC 그래픽 & 터치 보조모니터",
            font=font_sub,
            text_color="#94A3B8",
        ).pack(anchor="w", pady=(2, 0))

        header_right = ctk.CTkFrame(header, fg_color="transparent")
        header_right.pack(side="right", padx=16, pady=12)

        # Top Button to open/toggle separate Log Window
        self.btn_log_window = ctk.CTkButton(
            header_right,
            text="📋 로그 창 열기",
            font=font_top_btn,
            width=110,
            height=32,
            corner_radius=16,
            fg_color="#1E293B",
            hover_color="#334155",
            border_width=1,
            border_color="#334155",
            text_color="#E2E8F0",
            command=self._toggle_log_window,
        )
        self.btn_log_window.pack(side="right", padx=(8, 0))

        self.status_badge = ctk.CTkLabel(
            header_right,
            text=" ● 대기 중 ",
            font=font_top_btn,
            fg_color="#1E293B",
            text_color="#94A3B8",
            corner_radius=16,
            padx=12,
            pady=6,
        )
        self.status_badge.pack(side="right")

        # 2. Device & Display Status Row (2 Cards)
        cards_row = ctk.CTkFrame(self, fg_color="transparent")
        cards_row.pack(fill="x", padx=20, pady=6)
        cards_row.grid_columnconfigure(0, weight=1)
        cards_row.grid_columnconfigure(1, weight=1)

        # Card 1: USB Tablet Status
        card_tab = ctk.CTkFrame(cards_row, fg_color="#151C28", corner_radius=14, border_width=1, border_color="#263042")
        card_tab.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(card_tab, text="📱 연결된 태블릿 (USB)", font=font_card_title, text_color="#64748B").pack(
            anchor="w", padx=16, pady=(12, 2)
        )
        self.lbl_tablet_name = ctk.CTkLabel(card_tab, text="검색 중...", font=font_card_val, text_color="#E2E8F0")
        self.lbl_tablet_name.pack(anchor="w", padx=16, pady=(0, 2))
        self.lbl_tablet_sub = ctk.CTkLabel(card_tab, text="태블릿 앱에서 시작 시 자동 연동", font=font_sub, text_color="#64748B")
        self.lbl_tablet_sub.pack(anchor="w", padx=16, pady=(0, 12))

        # Card 2: Virtual Display Status
        card_mon = ctk.CTkFrame(cards_row, fg_color="#151C28", corner_radius=14, border_width=1, border_color="#263042")
        card_mon.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ctk.CTkLabel(card_mon, text="🖥️ 윈도우 가상 디스플레이", font=font_card_title, text_color="#64748B").pack(
            anchor="w", padx=16, pady=(12, 2)
        )
        self.lbl_mon_name = ctk.CTkLabel(card_mon, text="확인 중...", font=font_card_val, text_color="#E2E8F0")
        self.lbl_mon_name.pack(anchor="w", padx=16, pady=(0, 2))
        self.lbl_mon_sub = ctk.CTkLabel(card_mon, text="16:9 고정 비율 • 60Hz 주사율", font=font_sub, text_color="#38BDF8")
        self.lbl_mon_sub.pack(anchor="w", padx=16, pady=(0, 12))

        # 3. Resolution Control Card (Live Switching)
        res_card = ctk.CTkFrame(self, fg_color="#151C28", corner_radius=14, border_width=1, border_color="#263042")
        res_card.pack(fill="x", padx=20, pady=8)

        res_top = ctk.CTkFrame(res_card, fg_color="transparent")
        res_top.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(res_top, text="⚡ 해상도 설정 (실시간 전환 지원)", font=font_section, text_color="#F1F5F9").pack(
            side="left"
        )
        ctk.CTkLabel(
            res_top, text="작동 중에도 클릭 시 즉시 변경됩니다", font=font_sub, text_color="#64748B"
        ).pack(side="right")

        self.res_selector = ctk.CTkSegmentedButton(
            res_card,
            values=["1920 × 1080 (FHD 60p)", "1280 × 720 (HD 60p)"],
            command=self._on_resolution_selected,
            font=ctk.CTkFont(family="Malgun Gothic", size=14, weight="bold"),
            height=42,
            corner_radius=10,
            fg_color="#0B0F17",
            selected_color="#2563EB",
            selected_hover_color="#1D4ED8",
            unselected_color="#0B0F17",
            unselected_hover_color="#1E293B",
        )
        self.res_selector.pack(fill="x", padx=16, pady=(2, 14))
        self._sync_res_selector_ui()

        # 4. Telemetry Metrics Row (3 Boxes)
        metrics_row = ctk.CTkFrame(self, fg_color="transparent")
        metrics_row.pack(fill="x", padx=20, pady=6)
        metrics_row.grid_columnconfigure((0, 1, 2), weight=1)

        def make_metric_box(col, title, default_val, padx_tuple):
            box = ctk.CTkFrame(metrics_row, fg_color="#151C28", corner_radius=12, border_width=1, border_color="#263042")
            box.grid(row=0, column=col, sticky="nsew", padx=padx_tuple)
            ctk.CTkLabel(box, text=title, font=font_sub, text_color="#64748B").pack(pady=(12, 2))
            val_lbl = ctk.CTkLabel(box, text=default_val, font=font_card_val, text_color="#F8FAFC")
            val_lbl.pack(pady=(0, 12))
            return val_lbl

        self.lbl_fps = make_metric_box(0, "실시간 프레임", "0.0 FPS", (0, 5))
        self.lbl_bitrate = make_metric_box(1, "영상 비트레이트", "0.0 Mbps", (5, 5))
        self.lbl_touch = make_metric_box(2, "터치 ➔ 마우스", "대기 중", (5, 0))

        # 5. Primary Start / Stop Button
        self.btn_action = ctk.CTkButton(
            self,
            text="▶  보조모니터 시작  (갤럭시 탭 앱 자동 실행)",
            font=font_btn,
            height=56,
            corner_radius=14,
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF",
            command=self._on_toggle_stream,
        )
        self.btn_action.pack(fill="x", padx=20, pady=(14, 8))

        self.lbl_footer = ctk.CTkLabel(
            self,
            text="💡 양방향 연동: PC에서 시작하거나, 갤럭시 탭 앱에서 시작을 누르면 서로 자동으로 켜지고 연결됩니다.",
            font=ctk.CTkFont(family="Malgun Gothic", size=11),
            text_color="#64748B",
        )
        self.lbl_footer.pack(pady=(2, 12))

        self.log("TabMonitor Studio가 준비되었습니다.")

    def _sync_res_selector_ui(self):
        if self.target_resolution == (1280, 720):
            self.res_selector.set("1280 × 720 (HD 60p)")
        else:
            self.res_selector.set("1920 × 1080 (FHD 60p)")

    # ----------------- Separate Log Window Management -----------------
    def _toggle_log_window(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.destroy()
            self.log_window = None
            self.btn_log_window.configure(text="📋 로그 창 열기", fg_color="#1E293B")
        else:
            self.log_window = LogWindow(self, self.log_history, self._on_log_window_closed)
            self.btn_log_window.configure(text="📋 로그 창 닫기", fg_color="#2563EB")

    def _on_log_window_closed(self):
        self.log_window = None
        self.btn_log_window.configure(text="📋 로그 창 열기", fg_color="#1E293B")

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}\n"
        self.log_history.append(line)
        if len(self.log_history) > 500:
            self.log_history = self.log_history[-500:]
        self.after(0, self._append_log_ui, line)

    def _append_log_ui(self, line):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.append_line(line)

    # ----------------- Control / IPC Listener (Port 12355) -----------------
    def _start_ctrl_listener(self):
        def ctrl_loop():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind(("127.0.0.1", CTRL_PORT))
                srv.listen(5)
            except Exception:
                return
            while True:
                try:
                    conn, _ = srv.accept()
                    cmd = conn.recv(256).decode("utf-8", errors="ignore").strip()
                    conn.close()
                    if not cmd or cmd == "PING":
                        continue
                    elif cmd.startswith("AUTO_START"):
                        res = (1280, 720) if "1280" in cmd else (1920, 1080)
                        self.after(0, lambda r=res: self._handle_tablet_start_cmd(r))
                    elif cmd.startswith("RES_"):
                        res = (1280, 720) if "1280" in cmd else (1920, 1080)
                        self.after(0, lambda r=res: self._handle_tablet_res_cmd(r))
                    elif cmd == "STOP_FROM_TABLET":
                        self.after(0, lambda: self._stop_streaming(stop_tablet_app=False))
                    elif cmd == "SHOW":
                        self.after(0, self._bring_to_front)
                except Exception:
                    break

        threading.Thread(target=ctrl_loop, daemon=True).start()

    def _bring_to_front(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _handle_tablet_res_cmd(self, new_res):
        if new_res != self.target_resolution:
            self.target_resolution = new_res
            self._sync_res_selector_ui()
            label = "1280 × 720 (HD 60p)" if new_res == (1280, 720) else "1920 × 1080 (FHD 60p)"
            self._on_resolution_selected(label, force=True)

    def _handle_tablet_start_cmd(self, req_res):
        self._bring_to_front()
        if req_res in SUPPORTED_RESOLUTIONS and req_res != self.target_resolution:
            self.target_resolution = req_res
            self._sync_res_selector_ui()
        if not self.is_streaming and not self.is_starting:
            self.log("갤럭시 탭 앱에서 시작 요청을 수신하여 보조모니터를 시작합니다.")
            self._start_streaming(launched_from_tablet=True)

    # ----------------- Background Poller (USB & Telemetry) -----------------
    def _start_background_poller(self):
        def poll_loop():
            while True:
                try:
                    r = run_cmd_hidden([ADB_PATH, "devices"], timeout=3)
                    lines = [l.strip() for l in r.stdout.splitlines() if "\t" in l]
                    if lines and "device" in lines[0].split("\t")[1]:
                        serial = lines[0].split("\t")[0]
                        if not self.tablet_connected:
                            m = run_cmd_hidden([ADB_PATH, "shell", "getprop", "ro.product.model"], timeout=3)
                            model = m.stdout.strip() or "Galaxy Tab"
                            self.tablet_model = f"{model} ({serial})"
                            self.tablet_connected = True

                        # Ensure all 3 ports (video, touch, control) are reverse-forwarded
                        if self.reverse_configured_serial != serial:
                            run_cmd_hidden([ADB_PATH, "forward", "--remove-all"])
                            run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{TABLET_VIDEO_PORT}", f"tcp:{VIDEO_PORT}"])
                            run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{TABLET_TOUCH_PORT}", f"tcp:{TOUCH_PORT}"])
                            run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{CTRL_PORT}", f"tcp:{CTRL_PORT}"])
                            self.reverse_configured_serial = serial
                    elif lines and "authorizing" in lines[0]:
                        self.tablet_connected = False
                        self.reverse_configured_serial = None
                        self.tablet_model = "USB 디버깅 허용 대기 중"
                    else:
                        self.tablet_connected = False
                        self.reverse_configured_serial = None
                        self.tablet_model = "연결된 기기 없음"
                except Exception:
                    self.tablet_connected = False
                    self.reverse_configured_serial = None

                vdev = display_ctl.get_virtual_display_device()
                vres = display_ctl.get_virtual_display_resolution()

                self.after(0, self._update_dashboard_ui, vdev, vres)
                time.sleep(1.0)

        threading.Thread(target=poll_loop, daemon=True).start()

    def _update_dashboard_ui(self, vdev, vres):
        if self.tablet_connected:
            self.lbl_tablet_name.configure(text=self.tablet_model, text_color="#34D399")
            self.lbl_tablet_sub.configure(text="USB 초고속 데이터 및 양방향 제어 준비 완료", text_color="#94A3B8")
        else:
            self.lbl_tablet_name.configure(text=self.tablet_model, text_color="#F87171")
            self.lbl_tablet_sub.configure(text="태블릿을 USB 케이블로 연결해 주세요", text_color="#64748B")

        if vdev and vres:
            self.lbl_mon_name.configure(text=f"{vres[0]} × {vres[1]} @ 60Hz", text_color="#38BDF8")
            self.lbl_mon_sub.configure(text=f"가상 모니터 활성 ({vdev})", text_color="#94A3B8")
        else:
            self.lbl_mon_name.configure(text="비활성 (시작 시 자동 켜짐)", text_color="#FBBF24")
            self.lbl_mon_sub.configure(text="1920×1080 / 1280×720 60Hz", text_color="#64748B")

        if self.is_streaming:
            self.lbl_fps.configure(text=f"{self.live_fps:.1f} FPS", text_color="#34D399")
            self.lbl_bitrate.configure(text=f"{self.live_Mbps:.1f} Mbps", text_color="#38BDF8")
            self.lbl_touch.configure(text=self.last_touch_text, text_color="#A78BFA")
            self.status_badge.configure(text=" ● LIVE 송출 중 ", fg_color="#064E3B", text_color="#34D399")
        else:
            self.lbl_fps.configure(text="0.0 FPS", text_color="#64748B")
            self.lbl_bitrate.configure(text="0.0 Mbps", text_color="#64748B")
            self.lbl_touch.configure(text="대기 중", text_color="#64748B")
            if self.tablet_connected:
                self.status_badge.configure(text=" ● 연결 준비됨 ", fg_color="#1E3A8A", text_color="#60A5FA")
            else:
                self.status_badge.configure(text=" ● 대기 중 ", fg_color="#1E293B", text_color="#94A3B8")

    # ----------------- Resolution Change Handler -----------------
    def _on_resolution_selected(self, value, force=False):
        new_res = (1280, 720) if "1280" in value else (1920, 1080)
        if not force and new_res == self.target_resolution and display_ctl.get_virtual_display_resolution() == new_res:
            return

        self.target_resolution = new_res
        self.res_selector.configure(state="disabled")
        self.log(f"해상도 변경 요청: {new_res[0]}×{new_res[1]} @ 60Hz...")

        def switch_worker():
            ok, msg = display_ctl.set_virtual_display_resolution(new_res[0], new_res[1], 60)
            self.log(msg)
            if self.is_streaming:
                self.res_change_event.set()
                if self.current_video_client:
                    try:
                        self.current_video_client.close()
                    except Exception:
                        pass
            self.after(0, lambda: self.res_selector.configure(state="normal"))

        threading.Thread(target=switch_worker, daemon=True).start()

    # ----------------- Start / Stop Streaming Handler -----------------
    def _on_toggle_stream(self):
        if not self.is_streaming:
            self._start_streaming(launched_from_tablet=False)
        else:
            self._stop_streaming(stop_tablet_app=True)

    def _start_streaming(self, launched_from_tablet=False):
        if self.is_streaming or self.is_starting:
            return
        self.is_starting = True
        self.btn_action.configure(state="disabled", text="연결 및 스트리밍 준비 중...")
        self.stop_event.clear()
        self.res_change_event.clear()

        def startup_sequence():
            try:
                free_old_server_ports()

                cur_res = display_ctl.get_virtual_display_resolution()
                if cur_res != self.target_resolution:
                    self.log(f"가상 모니터 해상도를 {self.target_resolution[0]}×{self.target_resolution[1]}로 설정 중...")
                    display_ctl.set_virtual_display_resolution(self.target_resolution[0], self.target_resolution[1], 60)

                self.log("갤럭시 탭 USB 연결 확인 중...")
                r = run_cmd_hidden([ADB_PATH, "devices"])
                if "\tdevice" not in r.stdout:
                    self.log("[오류] 연결된 갤럭시 탭을 찾을 수 없습니다. USB 케이블을 확인하세요.")
                    self.is_starting = False
                    self.after(0, self._reset_start_button)
                    return

                self.log("초고속 USB 소켓 포트 포워딩 설정 중...")
                run_cmd_hidden([ADB_PATH, "forward", "--remove-all"])
                run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{TABLET_VIDEO_PORT}", f"tcp:{VIDEO_PORT}"])
                run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{TABLET_TOUCH_PORT}", f"tcp:{TOUCH_PORT}"])
                run_cmd_hidden([ADB_PATH, "reverse", f"tcp:{CTRL_PORT}", f"tcp:{CTRL_PORT}"])

                pkg_check = run_cmd_hidden([ADB_PATH, "shell", "pm", "path", "com.tabmonitor"])
                if "package:" not in pkg_check.stdout and os.path.exists(APK_PATH):
                    self.log("태블릿에 TabMonitor 앱 설치 중...")
                    run_cmd_hidden([ADB_PATH, "install", "-r", "-d", APK_PATH], timeout=20)

                self.is_streaming = True
                self.is_starting = False
                threading.Thread(target=self._touch_server_loop, daemon=True).start()
                threading.Thread(target=self._video_server_loop, daemon=True).start()

                time.sleep(0.25)

                if not launched_from_tablet:
                    self.log("갤럭시 탭 화면 켜기 및 보조모니터 앱 자동 실행 중...")
                    run_cmd_hidden([ADB_PATH, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
                    run_cmd_hidden([ADB_PATH, "shell", "wm", "dismiss-keyguard"])
                    run_cmd_hidden([ADB_PATH, "shell", "am", "force-stop", "com.tabmonitor"])
                    run_cmd_hidden([
                        ADB_PATH, "shell", "am", "start",
                        "-n", "com.tabmonitor/.MainActivity",
                        "--ez", "from_pc", "true"
                    ])
                else:
                    self.log("갤럭시 탭 앱 실행 감지 완료 — 스트리밍 즉시 연결!")

                self.log(f"스트리밍 시작 완료! ({self.target_resolution[0]}×{self.target_resolution[1]} @ 60fps)")

                self.after(
                    0,
                    lambda: self.btn_action.configure(
                        state="normal",
                        text="■  보조모니터 중지  (태블릿 앱 자동 종료)",
                        fg_color="#EF4444",
                        hover_color="#DC2626",
                    ),
                )
            except Exception as e:
                self.log(f"[오류] 시작 실패: {e}")
                self.is_streaming = False
                self.is_starting = False
                self.after(0, self._reset_start_button)

        threading.Thread(target=startup_sequence, daemon=True).start()

    def _stop_streaming(self, stop_tablet_app=True):
        self.log("스트리밍 중지 중...")
        self.is_streaming = False
        self.is_starting = False
        self.stop_event.set()

        for sock in (self.current_video_client, self.current_touch_client, self.video_server_sock, self.touch_server_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        if stop_tablet_app:
            def stop_tablet():
                try:
                    run_cmd_hidden([ADB_PATH, "shell", "am", "force-stop", "com.tabmonitor"])
                except Exception:
                    pass
                self.log("보조모니터 및 태블릿 앱이 종료되었습니다.")

            threading.Thread(target=stop_tablet, daemon=True).start()
        else:
            self.log("태블릿에서 스트리밍을 중지하여 대기 모드로 전환되었습니다.")

        self._reset_start_button()

    def _reset_start_button(self):
        self.btn_action.configure(
            state="normal",
            text="▶  보조모니터 시작  (갤럭시 탭 앱 자동 실행)",
            fg_color="#10B981",
            hover_color="#059669",
        )

    # ----------------- Streaming Engine Threads -----------------
    def _find_target_output_idx(self):
        outputs = dxcam.output_info()
        matches = re.findall(r"Output\[(\d+)\]:\s*Res:\((\d+),\s*(\d+)\)", outputs)
        for idx_str, w_str, h_str in matches:
            if (int(w_str), int(h_str)) in SUPPORTED_RESOLUTIONS and int(idx_str) > 0:
                return int(idx_str)
        if len(matches) > 1:
            return int(matches[1][0])
        return 0

    def _create_nvenc(self, width, height):
        codec = av.CodecContext.create("h264_nvenc", "w")
        codec.width = width
        codec.height = height
        codec.pix_fmt = "yuv420p"
        codec.time_base = Fraction(1, 60)
        codec.framerate = 60
        codec.max_b_frames = 0
        codec.bit_rate = 35_000_000 if width >= 1920 else 20_000_000
        codec.options = {
            "preset": "p4",
            "tune": "ull",
            "zerolatency": "1",
            "delay": "0",
            "rc": "vbr",
            "cq": "18",
            "g": "60",
        }
        codec.open()
        return codec

    def _video_server_loop(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", VIDEO_PORT))
            server.listen(1)
            server.settimeout(1.0)
            self.video_server_sock = server
        except Exception as e:
            self.log(f"[비디오 서버 오류] 포트 바인딩 실패: {e}")
            return

        camera = None
        try:
            while not self.stop_event.is_set():
                try:
                    client_sock, client_addr = server.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
                self.current_video_client = client_sock

                target_idx = self._find_target_output_idx()
                mon_rect = get_monitor_rect(target_idx)
                cur_w, cur_h = self.target_resolution
                self.res_change_event.clear()

                if camera is not None:
                    try:
                        camera.stop()
                    except Exception:
                        pass
                camera = dxcam.create(device_idx=0, output_idx=target_idx, output_color="BGRA")
                camera.start(target_fps=60, video_mode=True)

                codec = self._create_nvenc(cur_w, cur_h)
                self.log(f"태블릿 영상 스트림 연결됨 ({cur_w}×{cur_h} @ 60p NVENC)")

                pts = 0
                frame_interval = 1.0 / 60.0
                next_frame_time = time.perf_counter()
                last_bgra = None
                stat_frames = 0
                stat_bytes = 0
                stat_t0 = time.perf_counter()
                rect_check = 0

                try:
                    while not self.stop_event.is_set() and not self.res_change_event.is_set():
                        now = time.perf_counter()
                        sleep_time = next_frame_time - now
                        if sleep_time > 0.001:
                            time.sleep(sleep_time)
                        next_frame_time = max(now, next_frame_time + frame_interval)

                        rect_check += 1
                        if rect_check >= 60:
                            rect_check = 0
                            mon_rect = get_monitor_rect(target_idx)

                        try:
                            frame = camera.get_latest_frame()
                        except Exception:
                            frame = None

                        if frame is not None:
                            if frame.shape[1] != cur_w or frame.shape[0] != cur_h:
                                last_bgra = cv2.resize(frame, (cur_w, cur_h), interpolation=cv2.INTER_LINEAR)
                            elif not frame.flags.writeable:
                                last_bgra = frame.copy()
                            else:
                                last_bgra = frame

                        if last_bgra is None:
                            continue

                        cpos = get_cursor_on_monitor(mon_rect, cur_w, cur_h)
                        if cpos is not None:
                            cx, cy = cpos
                            x1, y1 = max(0, cx - 3), max(0, cy - 3)
                            x2, y2 = min(cur_w, cx + 22), min(cur_h, cy + 30)
                            saved_patch = last_bgra[y1:y2, x1:x2].copy()

                            pts_poly = CURSOR_SHAPE + [cx, cy]
                            cv2.polylines(last_bgra, [pts_poly], True, (0, 0, 0, 255), thickness=2, lineType=cv2.LINE_AA)
                            cv2.fillPoly(last_bgra, [pts_poly], (255, 255, 255, 255), lineType=cv2.LINE_AA)

                            yuv = cv2.cvtColor(last_bgra, cv2.COLOR_BGRA2YUV_I420)
                            last_bgra[y1:y2, x1:x2] = saved_patch
                        else:
                            yuv = cv2.cvtColor(last_bgra, cv2.COLOR_BGRA2YUV_I420)

                        vf = av.VideoFrame.from_ndarray(yuv, format="yuv420p")
                        vf.pts = pts
                        pts += 1

                        pkts = codec.encode(vf)
                        for pkt in pkts:
                            raw = bytes(pkt)
                            header = struct.pack(">I", len(raw))
                            client_sock.sendall(header + raw)
                            stat_bytes += len(raw)

                        stat_frames += 1
                        elapsed = time.perf_counter() - stat_t0
                        if elapsed >= 0.5:
                            self.live_fps = stat_frames / elapsed
                            self.live_Mbps = (stat_bytes * 8.0) / (elapsed * 1_000_000.0)
                            stat_frames = 0
                            stat_bytes = 0
                            stat_t0 = time.perf_counter()
                except Exception:
                    pass
                finally:
                    try:
                        client_sock.close()
                    except Exception:
                        pass
        finally:
            if camera is not None:
                try:
                    camera.stop()
                except Exception:
                    pass
            try:
                server.close()
            except Exception:
                pass
            self.live_fps = 0.0
            self.live_Mbps = 0.0

    def _touch_server_loop(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", TOUCH_PORT))
            server.listen(1)
            server.settimeout(1.0)
            self.touch_server_sock = server
        except Exception as e:
            self.log(f"[터치 서버 오류] 포트 바인딩 실패: {e}")
            return

        try:
            while not self.stop_event.is_set():
                try:
                    client_sock, client_addr = server.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.current_touch_client = client_sock
                target_idx = self._find_target_output_idx()
                mon_rect = get_monitor_rect(target_idx)
                self.log("태블릿 터치 ➔ 윈도우 마우스 제어 연결됨")

                try:
                    buf = bytearray()
                    ev_count = 0
                    while not self.stop_event.is_set():
                        chunk = client_sock.recv(1024)
                        if not chunk:
                            break
                        buf.extend(chunk)
                        while len(buf) >= 9:
                            action_type = buf[0]
                            norm_x, norm_y = struct.unpack(">ff", buf[1:9])
                            del buf[:9]
                            ev_count += 1
                            if ev_count % 20 == 1:
                                target_idx = self._find_target_output_idx()
                                mon_rect = get_monitor_rect(target_idx)
                            send_mouse_input(norm_x, norm_y, action_type, mon_rect)
                            act_str = "클릭" if action_type == 0 else ("드래그" if action_type == 1 else "뗌")
                            self.last_touch_text = f"{act_str} ({int(norm_x*100)}%, {int(norm_y*100)}%)"
                except Exception:
                    pass
                finally:
                    try:
                        client_sock.close()
                    except Exception:
                        pass
        finally:
            try:
                server.close()
            except Exception:
                pass

    def _on_close(self):
        self.stop_event.set()
        self.is_streaming = False
        try:
            run_cmd_hidden([ADB_PATH, "shell", "am", "force-stop", "com.tabmonitor"], timeout=2)
        except Exception:
            pass
        self.destroy()
        os._exit(0)


def notify_existing_instance(cmd="SHOW"):
    """If TabMonitorApp is already running, send command to it and return True."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.4)
        s.connect(("127.0.0.1", CTRL_PORT))
        s.sendall(cmd.encode("utf-8"))
        s.close()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    auto_start_flag = "--auto-start" in sys.argv
    init_res = None
    for arg in sys.argv[1:]:
        if arg == "1280x720":
            init_res = (1280, 720)
        elif arg == "1920x1080":
            init_res = (1920, 1080)

    # First check if an instance (already elevated) is running
    cmd_str = f"AUTO_START_{init_res[0]}x{init_res[1]}" if (auto_start_flag and init_res) else ("AUTO_START" if auto_start_flag else "SHOW")
    if notify_existing_instance(cmd_str):
        sys.exit(0)

    # Ensure elevated privileges (Admin) so SendInput can control elevated Windows applications without UIPI blocking
    if not ctypes.windll.shell32.IsUserAnAdmin() and "--no-elevate" not in sys.argv:
        try:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            args_str = " ".join([f'"{os.path.abspath(__file__)}"'] + [f'"{a}"' for a in sys.argv[1:]] + ['"--no-elevate"'])
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", pythonw, args_str, BASE_DIR, 1)
            if ret > 32:
                sys.exit(0)
        except Exception:
            pass

    if ctypes.windll.shell32.IsUserAnAdmin():
        # Register highest-privilege scheduled task so subsequent starts don't need a UAC prompt
        try:
            run_cmd_hidden([
                "schtasks", "/create", "/tn", "TabMonitorGUI",
                "/tr", f'"{os.path.join(BASE_DIR, "start_gui.bat")}"',
                "/sc", "once", "/st", "00:00", "/rl", "highest", "/f"
            ], timeout=3)
        except Exception:
            pass

    app = TabMonitorApp(auto_start=auto_start_flag, initial_res=init_res)
    app.mainloop()
