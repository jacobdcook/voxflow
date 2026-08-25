"""Windows backend: pynput hotkey, tkinter overlay, Ctrl+V paste."""

from __future__ import annotations

import ctypes
import math
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes

from mintflow.config import log
from mintflow.platform import BackendUnavailable

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError as e:
    raise BackendUnavailable(
        "The overlay needs Tk, which this Python was built without.\n"
        "  Reinstall Python from python.org and tick "
        '"tcl/tk and IDLE" during setup.'
    ) from e

try:
    from pynput import keyboard as pynput_keyboard
except ImportError as e:
    raise BackendUnavailable(
        "The hotkey needs pynput, which is not installed.\n"
        "  Fix it with:  pip install pynput"
    ) from e

try:
    import pyperclip as _pyperclip
except ImportError:
    _pyperclip = None

try:
    import winsound as _winsound
except ImportError:
    _winsound = None

TERMINAL_CLASSES = {
    "consolewindowclass",
    "cascadia_hosting_window_class",
    "mintty",
    "virtualconsoleclass",
    "putty",
    "kitty",
    "kitty_oswindow",
    "alacritty",
    "org.wezfurlong.wezterm",
    "console_2_main",
    "conemu",
    "conemu64",
    "pseudoconsolewindow",
    "cygwin/x xrl",
}

TERMINAL_EXES = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "windowsterminal.exe",
    "wt.exe",
    "mintty.exe",
    "bash.exe",
    "alacritty.exe",
    "wezterm.exe",
    "wezterm-gui.exe",
    "kitty.exe",
    "hyper.exe",
    "tabby.exe",
    "warp.exe",
    "putty.exe",
    "conemu.exe",
    "conemu64.exe",
    "windowsterminal.exe",
    "fluent-terminal.exe",
    "terminus.exe",
    "winterm.exe",
}

MOD_ALIASES = {
    "shift": "shift",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "super": "win",
    "win": "win",
    "meta": "win",
    "cmd": "win",
    "command": "win",
}

MODIFIER_KEYSYMS = {
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Alt_L",
    "Alt_R",
    "Meta_L",
    "Meta_R",
    "Super_L",
    "Super_R",
    "Win_L",
    "Win_R",
    "Caps_Lock",
    "Num_Lock",
    "ISO_Level3_Shift",
}

SOUND_CANDIDATES = {
    "start": (
        r"C:\Windows\Media\Windows Notify.wav",
        r"C:\Windows\Media\Speech On.wav",
        r"C:\Windows\Media\Windows Information Bar.wav",
        r"C:\Windows\Media\Windows Balloon.wav",
    ),
    "done": (
        r"C:\Windows\Media\Windows Ding.wav",
        r"C:\Windows\Media\Speech Off.wav",
        r"C:\Windows\Media\notify.wav",
    ),
    "error": (
        r"C:\Windows\Media\Windows Error.wav",
        r"C:\Windows\Media\Windows Foreground.wav",
        r"C:\Windows\Media\chord.wav",
        r"C:\Windows\Media\Windows Critical Stop.wav",
    ),
}

SOUND_ALIASES = {
    "start": "SystemNotification",
    "done": "SystemAsterisk",
    "error": "SystemHand",
}

SOUND_BEEPS = {
    "start": (880, 80),
    "done": (1200, 60),
    "error": (400, 180),
}

COMPACT_W = 280
COMPACT_H = 64
EXPANDED_W = 500
HEADER_H = 64
TEXT_MAX_W = 460
TEXT_MAX_LINES = 3
TEXT_LINE_H = 18
BOTTOM_MARGIN = 72

_INJECT_LOCK = threading.Lock()

PILL_FILL = "#121214"
PILL_OUTLINE = "#2A2A2C"
LABEL_FILL = "#8C8C8C"
TEXT_FILL = "#D9D9D9"
BAR_LISTENING = "#8AD8FF"
BAR_CLEANING = "#FFD94D"
BAR_DONE = "#8DEB9F"
BAR_ERROR = "#FF7373"
BAR_TRANSCRIBING = "#E0E0E0"
CHROMA = "#010103"

STATUS_LABELS = {
    "listening": "Listening",
    "transcribing": "Transcribing",
    "cleaning": "Cleaning up",
    "done": "Done",
    "error": "Try again",
    "loading": "Loading",
}

_KEY = pynput_keyboard.Key
_KeyCode = pynput_keyboard.KeyCode

_SPECIAL_KEYS = {
    "space": _KEY.space,
    "tab": _KEY.tab,
    "enter": _KEY.enter,
    "return": _KEY.enter,
    "esc": _KEY.esc,
    "escape": _KEY.esc,
    "backspace": _KEY.backspace,
    "delete": _KEY.delete,
    "up": _KEY.up,
    "down": _KEY.down,
    "left": _KEY.left,
    "right": _KEY.right,
    "home": _KEY.home,
    "end": _KEY.end,
    "page_up": _KEY.page_up,
    "page_down": _KEY.page_down,
    "caps_lock": _KEY.caps_lock,
    "insert": getattr(_KEY, "insert", None),
    "print": getattr(_KEY, "print_screen", None),
    "print_screen": getattr(_KEY, "print_screen", None),
    "scroll_lock": getattr(_KEY, "scroll_lock", None),
    "scrolllock": getattr(_KEY, "scroll_lock", None),
    "menu": getattr(_KEY, "menu", None),
    "num_lock": getattr(_KEY, "num_lock", None),
}
_SPECIAL_KEYS = {k: v for k, v in _SPECIAL_KEYS.items() if v is not None}
if hasattr(_KEY, "pause"):
    _SPECIAL_KEYS["pause"] = _KEY.pause
    _SPECIAL_KEYS["break"] = _KEY.pause

LLKHF_INJECTED = 0x10
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_PAUSE = 0x13

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
MONITOR_DEFAULTTONEAREST = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
CREATE_NO_WINDOW = 0x08000000
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105

_MOD_VKS = {
    VK_SHIFT: "shift",
    VK_LSHIFT: "shift",
    VK_RSHIFT: "shift",
    VK_CONTROL: "ctrl",
    VK_LCONTROL: "ctrl",
    VK_RCONTROL: "ctrl",
    VK_MENU: "alt",
    VK_LMENU: "alt",
    VK_RMENU: "alt",
    VK_LWIN: "win",
    VK_RWIN: "win",
}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _get_window_long():
    user32 = _user32()
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        fn = user32.GetWindowLongPtrW
        fn.argtypes = [wintypes.HWND, ctypes.c_int]
        fn.restype = ctypes.c_ssize_t
        return fn
    fn = user32.GetWindowLongW
    fn.argtypes = [wintypes.HWND, ctypes.c_int]
    fn.restype = ctypes.c_long
    return fn


def _set_window_long():
    user32 = _user32()
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        fn = user32.SetWindowLongPtrW
        fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        fn.restype = ctypes.c_ssize_t
        return fn
    fn = user32.SetWindowLongW
    fn.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    fn.restype = ctypes.c_long
    return fn


def _mod_map() -> dict:
    mapping = {}
    for attr, name in (
        ("shift", "shift"),
        ("shift_l", "shift"),
        ("shift_r", "shift"),
        ("ctrl", "ctrl"),
        ("ctrl_l", "ctrl"),
        ("ctrl_r", "ctrl"),
        ("alt", "alt"),
        ("alt_l", "alt"),
        ("alt_r", "alt"),
        ("cmd", "win"),
        ("cmd_l", "win"),
        ("cmd_r", "win"),
    ):
        key = getattr(_KEY, attr, None)
        if key is not None:
            mapping[key] = name
    return mapping


_MOD_KEYS = _mod_map()


def parse_hotkey(spec: str) -> tuple[set[str], str]:
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    key = parts[-1]
    mods: set[str] = set()
    for part in parts[:-1]:
        if part not in MOD_ALIASES:
            raise ValueError(f"unknown modifier {part}")
        mods.add(MOD_ALIASES[part])
    return mods, key


def is_typing_key(hotkey_spec: str) -> bool:
    """True when the hotkey's base key produces a character when tapped."""
    _mods, key = parse_hotkey(hotkey_spec)
    if key.startswith("keycode:"):
        return False
    return len(key) == 1 or key == "space"


def _vk_of(key) -> int | None:
    if key is None:
        return None
    vk = getattr(key, "vk", None)
    if vk is not None:
        return int(vk)
    val = getattr(key, "value", None)
    if val is not None:
        vk = getattr(val, "vk", None)
        if vk is not None:
            return int(vk)
    return None


def _mod_name(key) -> str | None:
    if key is None:
        return None
    if key in _MOD_KEYS:
        return _MOD_KEYS[key]
    vk = _vk_of(key)
    if vk is not None and vk in _MOD_VKS:
        return _MOD_VKS[vk]
    val = getattr(key, "value", None)
    for mod_key, name in _MOD_KEYS.items():
        if key == mod_key or val is not None and val == getattr(mod_key, "value", None):
            return name
    return None


def _resolve_pynput_key(name: str):
    if name.startswith("keycode:"):
        try:
            vk = int(name.split(":", 1)[1])
        except ValueError:
            return None
        return _KeyCode.from_vk(vk)
    if name in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[name]
    if re.fullmatch(r"f\d{1,2}", name):
        key = getattr(_KEY, name, None)
        if key is not None:
            return key
    if len(name) == 1:
        return _KeyCode.from_char(name)
    key = getattr(_KEY, name, None)
    if key is not None:
        return key
    return None


def _async_mods() -> set[str]:
    try:
        user32 = _user32()
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
    except Exception:
        return set()
    mods: set[str] = set()
    try:
        if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
            mods.add("shift")
        if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
            mods.add("ctrl")
        if user32.GetAsyncKeyState(VK_MENU) & 0x8000:
            mods.add("alt")
        if (user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or (
            user32.GetAsyncKeyState(VK_RWIN) & 0x8000
        ):
            mods.add("win")
    except Exception:
        return set()
    return mods


def _schedule_safe(schedule, fn) -> None:
    if not fn or not schedule:
        return
    try:
        schedule(fn)
    except Exception as e:
        log(f"callback: {e}")


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    pts = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=32, **kwargs)


def _pick_font(root: tk.Misc, size: int, weight: str = "normal") -> tkfont.Font:
    families = set(tkfont.families(root))
    for name in ("Segoe UI", "Calibri", "Tahoma", "Arial"):
        if name in families:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, family="TkDefaultFont", size=size, weight=weight)


# ---------------------------------------------------------------------------
# Overlay (tkinter Toplevel, Canvas waveform + streaming text)
# ---------------------------------------------------------------------------


class Overlay(tk.Toplevel):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("mintflow-overlay")
        self.overrideredirect(True)
        try:
            self.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        self.configure(bg=CHROMA)
        try:
            self.wm_attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            try:
                self.wm_attributes("-alpha", 0.92)
            except tk.TclError:
                pass
        try:
            self.resizable(False, False)
        except tk.TclError:
            pass

        self.status = "listening"
        self.level = 0.0
        self._text = ""
        self._bars = [0.12] * 18
        self._t0 = time.monotonic()
        self._win_w = COMPACT_W
        self._win_h = COMPACT_H
        self._tick_id = None
        self._styled = False
        self._font_label = _pick_font(self, 12)
        self._font_body = _pick_font(self, 13)

        self.canvas = tk.Canvas(
            self,
            width=COMPACT_W,
            height=COMPACT_H,
            highlightthickness=0,
            bd=0,
            bg=CHROMA,
        )
        self.canvas.pack(fill="both", expand=True)
        self.geometry(f"{COMPACT_W}x{COMPACT_H}")
        self.withdraw()
        self._redraw()

    def _hwnd(self) -> int:
        self.update_idletasks()
        return int(self.winfo_id())

    def _apply_win_style(self) -> None:
        if self._styled:
            return
        try:
            hwnd = self._hwnd()
            get_long = _get_window_long()
            set_long = _set_window_long()
            style = get_long(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            set_long(hwnd, GWL_EXSTYLE, style)
            user32 = _user32()
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            self._styled = True
        except Exception as e:
            log(f"overlay style: {e}")

    def _expanded(self) -> bool:
        return bool(self._text.strip()) and self.status == "listening"

    def _wrap_lines(self, text: str) -> list[str]:
        words = text.split()
        if not words:
            return []
        lines: list[str] = []
        current = ""
        for word in words:
            trial = word if not current else f"{current} {word}"
            if self._font_body.measure(trial) <= TEXT_MAX_W:
                current = trial
            else:
                if current:
                    lines.append(current)
                if self._font_body.measure(word) <= TEXT_MAX_W:
                    current = word
                else:
                    chunk = word
                    while chunk:
                        lo, hi = 1, len(chunk)
                        fit = 1
                        while lo <= hi:
                            mid = (lo + hi) // 2
                            if self._font_body.measure(chunk[:mid]) <= TEXT_MAX_W:
                                fit = mid
                                lo = mid + 1
                            else:
                                hi = mid - 1
                        lines.append(chunk[:fit])
                        chunk = chunk[fit:]
                    current = ""
        if current:
            lines.append(current)
        return lines

    def _sync_size(self) -> None:
        if self._expanded():
            lines = self._wrap_lines(self._text)
            vis = min(len(lines), TEXT_MAX_LINES)
            vis_h = vis * TEXT_LINE_H + 4
            w = EXPANDED_W
            h = max(120, min(180, HEADER_H + 10 + vis_h + 16))
        else:
            w, h = COMPACT_W, COMPACT_H
        if (w, h) == (self._win_w, self._win_h):
            self._redraw()
            return
        self._win_w, self._win_h = w, h
        self.canvas.config(width=w, height=h)
        self.geometry(f"{w}x{h}")
        if self.winfo_viewable():
            self.place_on_pointer_monitor()
        self._redraw()

    def place_on_pointer_monitor(self) -> None:
        w, h = self._win_w, self._win_h
        try:
            user32 = _user32()
            hmon = None
            # The text lands in the focused window, so prefer that monitor. The
            # pointer is often parked on a different screen entirely.
            try:
                user32.GetForegroundWindow.restype = wintypes.HWND
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    user32.MonitorFromWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
                    user32.MonitorFromWindow.restype = ctypes.c_void_p
                    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            except Exception:
                hmon = None
            if not hmon:
                pt = POINT()
                user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
                user32.GetCursorPos.restype = wintypes.BOOL
                if not user32.GetCursorPos(ctypes.byref(pt)):
                    raise OSError("GetCursorPos failed")
                user32.MonitorFromPoint.argtypes = [POINT, ctypes.c_uint]
                user32.MonitorFromPoint.restype = ctypes.c_void_p
                hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            if not hmon:
                raise OSError("could not resolve a monitor")
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                raise OSError("GetMonitorInfoW failed")
            geo = mi.rcWork  # excludes the taskbar
            x = geo.left + (geo.right - geo.left - w) // 2
            y = geo.bottom - h - min(BOTTOM_MARGIN, max(0, geo.bottom - geo.top - h))
            self.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
            return
        except Exception:
            pass
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
        except tk.TclError:
            return
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{sh - h - BOTTOM_MARGIN}")

    def set_status(self, status: str, level: float | None = None) -> None:
        self.status = status
        if level is not None:
            self.level = max(0.0, min(1.0, level))
        self._sync_size()

    def set_text(self, text: str) -> None:
        self._text = text or ""
        self._sync_size()

    def reset(self) -> None:
        self._text = ""
        self.status = "listening"
        self.level = 0.0
        self._sync_size()

    def show(self) -> None:
        self.deiconify()
        try:
            self.lift()
            self.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        self._apply_win_style()
        self._redraw()
        if self._tick_id is None:
            self._tick()

    def hide(self) -> None:
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None
        self.withdraw()
        self.reset()

    def _tick(self) -> None:
        self._tick_id = None
        if not self.winfo_viewable():
            return
        target = 0.15 + self.level * 0.85
        t = time.monotonic() - self._t0
        n = len(self._bars)
        for i in range(n):
            if self.status == "listening":
                phase = (i / n) * 3.14
                want = target * (0.45 + 0.55 * abs(math.sin(t * 6 + phase)))
            elif self.status == "transcribing":
                want = 0.25 + 0.35 * abs(math.sin(t * 4 + i * 0.4))
            elif self.status == "cleaning":
                want = 0.2 + 0.25 * abs(math.sin(t * 3 + i * 0.5))
            else:
                want = 0.55
            self._bars[i] += (want - self._bars[i]) * 0.28
        self._redraw()
        self._tick_id = self.after(33, self._tick)

    def _bar_color(self) -> str:
        if self.status == "listening":
            return BAR_LISTENING
        if self.status == "cleaning":
            return BAR_CLEANING
        if self.status == "done":
            return BAR_DONE
        if self.status == "error":
            return BAR_ERROR
        return BAR_TRANSCRIBING

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = self._win_w, self._win_h
        expanded = self._expanded()
        radius = (COMPACT_H / 2) if not expanded else 20
        _round_rect(
            c, 0.5, 0.5, w - 0.5, h - 0.5, radius,
            fill=PILL_FILL, outline=PILL_OUTLINE, width=1,
        )
        label = STATUS_LABELS.get(self.status, self.status)
        c.create_text(
            22,
            18 + 6,
            text=label,
            fill=LABEL_FILL,
            font=self._font_label,
            anchor="w",
        )
        n = len(self._bars)
        gap = 3
        bar_w = 6
        total = n * bar_w + (n - 1) * gap
        x0 = (w - total) / 2
        y_mid = HEADER_H * 0.62
        max_h = HEADER_H * 0.42
        color = self._bar_color()
        for i, b in enumerate(self._bars):
            bh = max(4, b * max_h)
            x = x0 + i * (bar_w + gap)
            _round_rect(
                c, x, y_mid - bh / 2, x + bar_w, y_mid + bh / 2, 2,
                fill=color, outline="",
            )
        if expanded:
            self._draw_text()

    def _draw_text(self) -> None:
        lines = self._wrap_lines(self._text)
        vis = lines[-TEXT_MAX_LINES:]
        text_x, text_y = 20, HEADER_H + 4
        for i, line in enumerate(vis):
            self.canvas.create_text(
                text_x,
                text_y + i * TEXT_LINE_H,
                text=line,
                fill=TEXT_FILL,
                font=self._font_body,
                anchor="nw",
            )


# ---------------------------------------------------------------------------
# Clipboard / paste / terminal / sound
# ---------------------------------------------------------------------------


def _window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        user32 = _user32()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        n = user32.GetClassNameW(hwnd, buf, 256)
        return buf.value if n else ""
    except Exception:
        return ""


def _window_exe(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        user32 = _user32()
        kernel32 = _kernel32()
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def focused_is_terminal() -> bool:
    try:
        user32 = _user32()
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = int(user32.GetForegroundWindow() or 0)
    except Exception:
        return False
    if not hwnd:
        return False
    cls = _window_class(hwnd).lower()
    if cls in TERMINAL_CLASSES:
        return True
    if any(term in cls for term in TERMINAL_CLASSES):
        return True
    exe = os.path.basename(_window_exe(hwnd)).lower()
    if exe in TERMINAL_EXES:
        return True
    return False


def _open_clipboard(retries: int = 8) -> bool:
    user32 = _user32()
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    for i in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.01 * (i + 1))
    return False


def _win32_clipboard_get() -> str:
    user32 = _user32()
    kernel32 = _kernel32()
    if not _open_clipboard():
        return ""
    try:
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win32_clipboard_set(text: str) -> None:
    user32 = _user32()
    kernel32 = _kernel32()
    data = (text or "").encode("utf-16-le") + b"\x00\x00"
    if not _open_clipboard():
        raise OSError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise OSError("GlobalAlloc failed")
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            raise OSError("GlobalLock failed")
        ctypes.memmove(locked, data, len(data))
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def clipboard_get() -> str:
    if _pyperclip is not None:
        try:
            return _pyperclip.paste() or ""
        except Exception:
            pass
    try:
        return _win32_clipboard_get()
    except Exception:
        return ""


def clipboard_set(text: str) -> None:
    value = text or ""
    if _pyperclip is not None:
        try:
            _pyperclip.copy(value)
            return
        except Exception:
            pass
    _win32_clipboard_set(value)


def _send_paste(terminal: bool) -> None:
    controller = pynput_keyboard.Controller()
    ctrl = _KEY.ctrl
    shift = _KEY.shift
    v_key = _KeyCode.from_char("v")
    controller.press(ctrl)
    try:
        if terminal:
            controller.press(shift)
        try:
            controller.press(v_key)
            controller.release(v_key)
        finally:
            if terminal:
                controller.release(shift)
    finally:
        controller.release(ctrl)


def inject_text(text: str, restore_ms: int) -> None:
    if not text:
        return
    with _INJECT_LOCK:
        old = clipboard_get()
        clipboard_set(text)
        time.sleep(0.04)
        terminal = focused_is_terminal()
        try:
            _send_paste(terminal)
        except Exception as e:
            log(f"paste: {e}")

    def restore():
        time.sleep(max(0, restore_ms) / 1000)
        if not old:
            # Nothing (or non-text) was on the clipboard before. Wiping it would
            # be worse than leaving the dictated text there.
            return
        with _INJECT_LOCK:
            # Only undo our own paste. A newer dictation or a manual copy wins.
            try:
                if clipboard_get() != text:
                    return
                clipboard_set(old)
            except Exception:
                pass

    threading.Thread(target=restore, daemon=True, name="mintflow-clip").start()


def play_sound_file(kind: str, volume: float = 0.3) -> None:
    # Windows plays notification sounds at the system volume; mintflow can only
    # honour 0 (silent) or "play it". Everything else is the OS mixer's call.
    if max(0.0, min(1.0, volume)) <= 0:
        return
    for path in SOUND_CANDIDATES.get(kind, ()):
        if os.path.exists(path):
            if _winsound is not None:
                try:
                    _winsound.PlaySound(
                        path,
                        _winsound.SND_FILENAME
                        | _winsound.SND_ASYNC
                        | _winsound.SND_NODEFAULT,
                    )
                    return
                except Exception:
                    pass
            try:
                subprocess.Popen(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "(New-Object System.Media.SoundPlayer "
                        f"'{path.replace(chr(39), chr(39) * 2)}').Play();",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
                return
            except Exception:
                pass
    if _winsound is not None:
        alias = SOUND_ALIASES.get(kind)
        if alias:
            try:
                _winsound.PlaySound(
                    alias, _winsound.SND_ALIAS | _winsound.SND_ASYNC | _winsound.SND_NODEFAULT
                )
                return
            except Exception:
                pass
        try:
            freq, dur = SOUND_BEEPS.get(kind, (800, 80))
            _winsound.Beep(freq, dur)
            return
        except Exception:
            pass
    freq, dur = SOUND_BEEPS.get(kind, (800, 80))
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"[console]::beep({freq},{dur})",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# pynput hold-to-talk (SetWindowsHookEx, suppress hotkey only)
# ---------------------------------------------------------------------------


class HotkeyGrabber:
    def __init__(
        self,
        spec: str,
        on_press=None,
        on_release=None,
        schedule=None,
        tap_ms: int = 220,
        repeat_ms: int = 80,
    ) -> None:
        self.spec = spec
        self.on_press = on_press
        self.on_release = on_release
        self._schedule = schedule
        self.tap_ms = tap_ms
        self.repeat_ms = repeat_ms
        self._required_mods, self._keyname = parse_hotkey(spec)
        self._pynput_key = _resolve_pynput_key(self._keyname)
        self._vk = _vk_of(self._pynput_key)
        self._char = self._keyname if len(self._keyname) == 1 else None
        if self._keyname == "space":
            self._char = " "
        self._listener: pynput_keyboard.Listener | None = None
        self._controller = pynput_keyboard.Controller()
        self._held = False
        self._replaying = False
        self._block_vk: int | None = None
        self._live_mods: set[str] = set()
        self._repeat_timer: threading.Timer | None = None

    def start(self) -> None:
        if self._pynput_key is None and not self._keyname.startswith("keycode:"):
            log(f"cannot resolve key {self._keyname}")
        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=False,
            win32_event_filter=self._win32_filter,
        )
        self._listener.start()
        try:
            self._listener.wait()
        except Exception:
            pass
        log(f"grabbed {self.spec}")

    def stop(self) -> None:
        self._cancel_repeat()
        self._held = False
        self._block_vk = None
        listener = self._listener
        self._listener = None
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass

    def replay_tap(self) -> None:
        key = self._pynput_key
        if key is None:
            return
        self._replaying = True
        try:
            self._controller.press(key)
            self._controller.release(key)
            time.sleep(0.05)
        except Exception as e:
            log(f"tap replay: {e}")
        finally:
            self._replaying = False

    def _cancel_repeat(self) -> None:
        if self._repeat_timer is not None:
            self._repeat_timer.cancel()
            self._repeat_timer = None

    def _matches_key(self, key) -> bool:
        if key is None:
            return False
        listener = self._listener
        if listener is not None:
            try:
                key = listener.canonical(key)
            except Exception:
                pass
        if self._keyname.startswith("keycode:"):
            try:
                want = int(self._keyname.split(":", 1)[1])
            except ValueError:
                return False
            return _vk_of(key) == want
        if self._vk is not None and _vk_of(key) == self._vk:
            return True
        char = getattr(key, "char", None)
        if self._char and char and char.lower() == self._char.lower():
            return True
        if self._pynput_key is not None and key == self._pynput_key:
            return True
        return False

    def _combo_down(self) -> bool:
        live = self._live_mods or _async_mods()
        return live == self._required_mods

    def _vk_is_hotkey(self, vk: int) -> bool:
        if vk in _MOD_VKS:
            return False
        if self._keyname.startswith("keycode:"):
            try:
                return vk == int(self._keyname.split(":", 1)[1])
            except ValueError:
                return False
        if self._vk is not None:
            return vk == self._vk
        if self._keyname == "pause":
            return vk == VK_PAUSE
        return False

    def _should_suppress_vk(self, vk: int) -> bool:
        if self._replaying:
            return False
        if self._block_vk is not None and vk == self._block_vk:
            return True
        if not self._vk_is_hotkey(vk):
            return False
        mods = self._live_mods or _async_mods()
        return mods == self._required_mods or self._held

    def _on_press(self, key, injected=False) -> None:
        if injected or self._replaying:
            return
        name = _mod_name(key)
        if name:
            self._live_mods.add(name)
            return
        if not self._matches_key(key) or not self._combo_down():
            return
        self._cancel_repeat()
        vk = _vk_of(key)
        if vk is not None:
            self._block_vk = vk
        if self._held:
            return
        self._held = True
        _schedule_safe(self._schedule, self.on_press)

    def _on_release(self, key, injected=False) -> None:
        if injected or self._replaying:
            return
        name = _mod_name(key)
        if name:
            self._live_mods.discard(name)
            return
        if not self._matches_key(key):
            return
        self._cancel_repeat()
        delay = max(0, self.repeat_ms) / 1000
        if delay <= 0:
            self._finish_release()
            return
        self._repeat_timer = threading.Timer(delay, self._finish_release)
        self._repeat_timer.daemon = True
        self._repeat_timer.start()

    def _finish_release(self) -> None:
        self._repeat_timer = None
        if not self._held:
            return
        self._held = False
        self._block_vk = None
        _schedule_safe(self._schedule, self.on_release)

    def _win32_filter(self, msg, data):
        listener = self._listener
        if listener is None:
            return True
        try:
            flags = int(getattr(data, "flags", 0) or 0)
            vk = int(getattr(data, "vkCode", 0) or 0)
        except Exception:
            listener._suppress = False
            return True
        injected = bool(flags & LLKHF_INJECTED)
        if injected or self._replaying:
            listener._suppress = False
            return True
        name = _MOD_VKS.get(vk)
        if name:
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self._live_mods.add(name)
            elif msg in (WM_KEYUP, WM_SYSKEYUP):
                self._live_mods.discard(name)
            listener._suppress = False
            return True
        listener._suppress = bool(self._should_suppress_vk(vk))
        return True


# ---------------------------------------------------------------------------
# Set-hotkey dialog
# ---------------------------------------------------------------------------


def capture_hotkey_dialog(timeout_s: float = 15, parent: tk.Misc | None = None) -> dict | None:
    captured: dict = {}
    owned = False
    if parent is None:
        parent = tk.Tk()
        parent.withdraw()
        owned = True

    win = tk.Toplevel(parent)
    win.title("mintflow hotkey")
    win.geometry("460x160")
    try:
        win.attributes("-topmost", True)
    except tk.TclError:
        pass
    win.resizable(False, False)

    remaining = {"n": int(timeout_s)}
    label = tk.Label(win, justify="center", font=("Segoe UI", 14))
    label.pack(expand=True, fill="both", padx=16, pady=16)

    def render() -> None:
        label.config(
            text=(
                "Press the key you want for mintflow\n"
                "Any key or combo. Esc cancels.\n"
                f"{remaining['n']}s left"
            )
        )

    def finish() -> None:
        try:
            win.grab_release()
        except Exception:
            pass
        win.destroy()

    def countdown() -> None:
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            finish()
            return
        render()
        try:
            win.after(1000, countdown)
        except tk.TclError:
            pass

    def on_key(ev) -> str:
        name = ev.keysym or ""
        if name == "Escape":
            finish()
            return "break"
        if name in MODIFIER_KEYSYMS:
            return "break"
        mods = []
        state = int(getattr(ev, "state", 0))
        if state & 0x4:
            mods.append("ctrl")
        if state & 0x8:
            mods.append("alt")
        if state & 0x40:
            mods.append("win")
        if state & 0x1:
            mods.append("shift")
        ch = getattr(ev, "char", "") or ""
        typing = bool(ch.strip())
        lower = name.lower()
        if len(name) == 1 and name.isalnum():
            base = name.lower()
        elif lower in ("pause", "break"):
            base = "pause"
        elif re.fullmatch(r"f\d{1,2}", name or "", re.I):
            base = name.lower()
        elif lower in ("space", "spacebar"):
            base = "space"
        else:
            code = getattr(ev, "keycode", None)
            base = f"keycode:{code}" if code is not None else lower
        captured["spec"] = "+".join(mods + [base])
        captured["label"] = "+".join([m.upper() for m in mods] + [name.upper()])
        captured["typing"] = typing
        finish()
        return "break"

    render()
    win.bind("<KeyPress>", on_key)
    win.protocol("WM_DELETE_WINDOW", finish)
    win.update_idletasks()
    try:
        win.grab_set()
    except tk.TclError:
        pass
    win.focus_force()
    win.lift()
    win.after(1000, countdown)
    try:
        parent.wait_window(win)
    finally:
        if owned:
            try:
                parent.destroy()
            except Exception:
                pass

    if "spec" not in captured:
        return None
    return captured


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class WindowsBackend:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("mintflow")
        self._overlay = Overlay(self._root)
        self._grabber: HotkeyGrabber | None = None

    def run(self) -> None:
        self._root.mainloop()

    def quit(self) -> None:
        self.hotkey_ungrab()
        try:
            self._root.quit()
        except Exception:
            pass
        try:
            self._root.destroy()
        except Exception:
            pass

    def call_on_main(self, fn, *args, **kwargs):
        def _go():
            try:
                result = fn(*args, **kwargs)
                if result:
                    return self._root.after(0, _go)
            except Exception as e:
                log(f"call_on_main: {e}")

        try:
            return self._root.after(0, _go)
        except tk.TclError as e:
            log(f"call_on_main: {e}")
            return None

    def call_later(self, ms, fn):
        interval = max(1, int(ms))
        handle = {"id": None, "dead": False}

        def _go():
            if handle["dead"]:
                return
            try:
                again = bool(fn())
            except Exception as e:
                log(f"timer: {e}")
                again = False
            if again and not handle["dead"]:
                try:
                    handle["id"] = self._root.after(interval, _go)
                except tk.TclError:
                    handle["id"] = None
            else:
                handle["id"] = None

        handle["id"] = self._root.after(interval, _go)
        return handle

    def call_later_s(self, s, fn):
        return self.call_later(max(1, int(float(s) * 1000)), fn)

    def cancel(self, handle) -> None:
        if not handle:
            return
        if isinstance(handle, dict):
            handle["dead"] = True
            hid = handle.get("id")
            handle["id"] = None
        else:
            hid = handle
        if hid is None:
            return
        try:
            self._root.after_cancel(hid)
        except Exception:
            pass

    def overlay_show(self) -> None:
        self._overlay.show()

    def overlay_hide(self) -> None:
        self._overlay.hide()

    def overlay_set_status(self, status, level=0.0) -> None:
        self._overlay.set_status(status, level)

    def overlay_set_text(self, text) -> None:
        self._overlay.set_text(text)

    def overlay_place(self) -> None:
        self._overlay.place_on_pointer_monitor()

    def hotkey_grab(self, spec, on_press, on_release) -> None:
        self.hotkey_ungrab()
        tap_ms = int(self.cfg.get("tap_ms", 220))
        repeat_ms = int(self.cfg.get("repeat_ms", 80))
        self._grabber = HotkeyGrabber(
            spec,
            on_press=on_press,
            on_release=on_release,
            schedule=self.call_on_main,
            tap_ms=tap_ms,
            repeat_ms=repeat_ms,
        )
        self._grabber.start()

    def hotkey_ungrab(self) -> None:
        if self._grabber is None:
            return
        try:
            self._grabber.stop()
        except Exception as e:
            log(f"hotkey ungrab: {e}")
        self._grabber = None

    def hotkey_replay_tap(self) -> None:
        if self._grabber is None:
            return
        try:
            self._grabber.replay_tap()
        except Exception as e:
            log(f"tap replay: {e}")

    def paste_text(self, text, restore_ms) -> None:
        if threading.current_thread() is threading.main_thread():
            threading.Thread(
                target=inject_text, args=(text, int(restore_ms)), daemon=True
            ).start()
        else:
            inject_text(text, int(restore_ms))

    def focused_is_terminal(self) -> bool:
        return focused_is_terminal()

    def play_sound(self, kind) -> None:
        play_sound_file(kind, float(self.cfg.get("sound_volume", 0.3)))

    def capture_hotkey(self, timeout_s=15) -> dict | None:
        return capture_hotkey_dialog(timeout_s, self._root)
