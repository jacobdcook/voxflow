"""macOS backend: pynput hotkey, tkinter overlay, pbcopy paste."""

from __future__ import annotations

import math
import os
import re
import subprocess
import threading
import time

from voxflow.config import log
from voxflow.platform import BackendUnavailable

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError as e:
    raise BackendUnavailable(
        "The overlay needs Tk, which this Python was built without.\n"
        "  Fix it with:  brew install python-tk\n"
        "  Then run the installer again."
    ) from e

try:
    from pynput import keyboard as pynput_keyboard
except ImportError as e:
    raise BackendUnavailable(
        "The hotkey needs pynput, which is not installed.\n"
        "  Fix it with:  pip3 install pynput"
    ) from e

TERMINAL_NAMES = {
    "terminal",
    "iterm2",
    "iterm",
    "alacritty",
    "kitty",
    "wezterm",
    "wezterm-gui",
    "hyper",
    "warp",
    "tabby",
    "ghostty",
    "cool-retro-term",
    "kitty-direct",
}

MOD_ALIASES = {
    "shift": "shift",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "super": "cmd",
    "win": "cmd",
    "meta": "cmd",
    "cmd": "cmd",
    "command": "cmd",
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
    "Command",
    "Command_L",
    "Command_R",
    "Option_L",
    "Option_R",
    "Caps_Lock",
    "ISO_Level3_Shift",
}

SOUND_CANDIDATES = {
    "start": (
        "/System/Library/Sounds/Tink.aiff",
        "/System/Library/Sounds/Blow.aiff",
    ),
    "done": (
        "/System/Library/Sounds/Pop.aiff",
        "/System/Library/Sounds/Glass.aiff",
    ),
    "error": (
        "/System/Library/Sounds/Basso.aiff",
        "/System/Library/Sounds/Sosumi.aiff",
    ),
}

COMPACT_W = 280
COMPACT_H = 64
EXPANDED_W = 500
HEADER_H = 64
TEXT_MAX_W = 460
TEXT_MAX_LINES = 3
TEXT_LINE_H = 18
BOTTOM_MARGIN = 72

# pbcopy/pbpaste/osascript can wedge when another app stalls the pasteboard.
CMD_TIMEOUT_S = 3.0
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
}
if hasattr(_KEY, "f13"):
    _SPECIAL_KEYS["pause"] = _KEY.f13
    _SPECIAL_KEYS["break"] = _KEY.f13
if hasattr(_KEY, "pause"):
    _SPECIAL_KEYS["pause"] = _KEY.pause
    _SPECIAL_KEYS["break"] = _KEY.pause

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
        ("cmd", "cmd"),
        ("cmd_l", "cmd"),
        ("cmd_r", "cmd"),
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
    for name in ("SF Pro Text", "Helvetica Neue", "Helvetica", "Lucida Grande"):
        if name in families:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, family="TkDefaultFont", size=size, weight=weight)


# ---------------------------------------------------------------------------
# Overlay (tkinter Toplevel, Canvas waveform + streaming text)
# ---------------------------------------------------------------------------


class Overlay(tk.Toplevel):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        self.title("voxflow-overlay")
        self.overrideredirect(True)
        try:
            self.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        self._transparent = False
        try:
            self.wm_attributes("-transparent", True)
            self.configure(bg="systemTransparent")
            self._transparent = True
        except tk.TclError:
            self.configure(bg=PILL_FILL)
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

        bg = "systemTransparent" if self._transparent else PILL_FILL
        self.canvas = tk.Canvas(
            self,
            width=COMPACT_W,
            height=COMPACT_H,
            highlightthickness=0,
            bd=0,
            bg=bg,
        )
        self.canvas.pack(fill="both", expand=True)
        self.geometry(f"{COMPACT_W}x{COMPACT_H}")
        try:
            self.tk.call(
                "::tk::unsupported::MacWindowStyle",
                "style",
                self._w,
                "help",
                "noActivates",
                "noShadow",
            )
        except tk.TclError:
            pass
        self.withdraw()
        self._redraw()

    def _apply_mac_style(self) -> None:
        if self._styled:
            return
        try:
            self.update_idletasks()
            from AppKit import NSApp, NSStatusWindowLevel

            for nsw in NSApp.windows():
                try:
                    title = str(nsw.title()) if nsw.title() else ""
                except Exception:
                    title = ""
                if title != "voxflow-overlay":
                    continue
                try:
                    nsw.setIgnoresMouseEvents_(True)
                except Exception:
                    pass
                try:
                    nsw.setLevel_(NSStatusWindowLevel)
                except Exception:
                    pass
        except Exception:
            pass
        self._styled = True

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
            from AppKit import NSEvent, NSScreen

            mouse = NSEvent.mouseLocation()
            screens = list(NSScreen.screens() or [])
            if not screens:
                raise RuntimeError("no screens")
            target = screens[0]
            for screen in screens:
                f = screen.frame()
                if (
                    f.origin.x <= mouse.x < f.origin.x + f.size.width
                    and f.origin.y <= mouse.y < f.origin.y + f.size.height
                ):
                    target = screen
                    break
            # visibleFrame excludes the Dock and the menu bar, so the overlay is
            # never hidden behind them.
            f = target.visibleFrame()
            cocoa_x = f.origin.x + (f.size.width - w) / 2
            cocoa_y = f.origin.y + min(BOTTOM_MARGIN, max(0, f.size.height - h))
            # Tk's y axis starts at the top of the *primary* screen (screens[0]),
            # not at the top of whichever screen sits highest. Using the highest
            # screen puts the overlay off-screen whenever a second display is
            # arranged above the primary one.
            primary = screens[0].frame()
            flip_h = primary.origin.y + primary.size.height
            x = int(round(cocoa_x))
            y = int(round(flip_h - cocoa_y - h))
            self.geometry(f"{w}x{h}+{x}+{y}")
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
        self._apply_mac_style()
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


def focused_is_terminal() -> bool:
    try:
        raw = subprocess.check_output(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first process whose frontmost is true',
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=CMD_TIMEOUT_S,
        ).strip()
    except Exception:
        return False
    name = raw.lower().replace(".app", "")
    if name in TERMINAL_NAMES:
        return True
    return any(term in name for term in TERMINAL_NAMES)


def clipboard_get() -> bytes:
    try:
        return subprocess.check_output(
            ["pbpaste"], stderr=subprocess.DEVNULL, timeout=CMD_TIMEOUT_S
        )
    except Exception:
        return b""


def clipboard_set_bytes(data: bytes) -> None:
    p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    try:
        p.communicate(data or b"", timeout=CMD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        log("pbcopy timed out while setting the clipboard")


def clipboard_set(text: str) -> None:
    clipboard_set_bytes((text or "").encode("utf-8"))


def inject_text(text: str, restore_ms: int) -> None:
    if not text:
        return
    payload = text.encode("utf-8")
    with _INJECT_LOCK:
        old = clipboard_get()
        clipboard_set_bytes(payload)
        time.sleep(0.04)
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CMD_TIMEOUT_S,
        )

    def restore():
        time.sleep(max(0, restore_ms) / 1000)
        if not old:
            # Nothing (or non-text) was on the clipboard before. Wiping it would
            # be worse than leaving the dictated text there.
            return
        with _INJECT_LOCK:
            # Only undo our own paste. A newer dictation or a manual copy wins.
            if clipboard_get() != payload:
                return
            clipboard_set_bytes(old)

    threading.Thread(target=restore, daemon=True, name="voxflow-clip").start()


def play_sound_file(kind: str, volume: float = 0.3) -> None:
    level = max(0.0, min(1.0, volume))
    if level <= 0:
        return
    for path in SOUND_CANDIDATES.get(kind, ()):
        if os.path.exists(path):
            try:
                subprocess.Popen(
                    ["afplay", "-v", f"{level:.3f}", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                log(f"afplay: {e}")
            return


# ---------------------------------------------------------------------------
# pynput hold-to-talk (CGEventTap, suppress hotkey only)
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
        self.on_cancel = None
        self._cancel_armed = False

    def set_cancel_armed(self, active: bool) -> None:
        self._cancel_armed = bool(active)

    def start(self) -> None:
        if self._pynput_key is None and not self._keyname.startswith("keycode:"):
            log(f"cannot resolve key {self._keyname}")
        self._listener = pynput_keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=True,
            darwin_intercept=self._intercept,
        )
        self._listener.start()
        try:
            self._listener.wait()
        except Exception:
            pass
        trusted = getattr(type(self._listener), "IS_TRUSTED", True)
        if not trusted:
            log("macOS accessibility permission missing: hotkey grab will not work")
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
        return self._live_mods == self._required_mods

    def _on_press(self, key, injected=False) -> None:
        if injected or self._replaying:
            return
        if self._cancel_armed and key in (
            pynput_keyboard.Key.esc,
            pynput_keyboard.Key.end,
        ):
            if self.on_cancel:
                _schedule_safe(self._schedule, self.on_cancel)
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

    def _event_vk(self, event) -> int | None:
        try:
            from Quartz import CGEventGetIntegerValueField, kCGKeyboardEventKeycode

            return int(CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode))
        except Exception:
            return None

    def _event_injected(self, event) -> bool:
        try:
            from Quartz import CGEventGetIntegerValueField, kCGEventSourceUnixProcessID

            return CGEventGetIntegerValueField(event, kCGEventSourceUnixProcessID) != 0
        except Exception:
            return False

    def _intercept(self, event_type, event):
        if self._replaying or self._event_injected(event):
            return event
        vk = self._event_vk(event)
        if vk is None:
            return None if self._held else event
        if self._block_vk is not None and vk == self._block_vk:
            return None
        if self._vk is not None and vk == self._vk and (self._held or self._combo_down()):
            return None
        if self._held and vk == self._block_vk:
            return None
        return event


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
    win.title("voxflow hotkey")
    win.geometry("460x160")
    try:
        win.attributes("-topmost", True)
    except tk.TclError:
        pass
    win.resizable(False, False)

    remaining = {"n": int(timeout_s)}
    label = tk.Label(win, justify="center", font=("Helvetica", 14))
    label.pack(expand=True, fill="both", padx=16, pady=16)

    def render() -> None:
        label.config(
            text=(
                "Press the key you want for voxflow\n"
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
        if state & 0x10:
            mods.append("cmd")
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


class MacBackend:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("voxflow")
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

    def hotkey_grab(self, spec, on_press, on_release, on_cancel=None) -> None:
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
        self._grabber.on_cancel = on_cancel
        self._grabber.start()

    def hotkey_set_cancel(self, active: bool) -> None:
        if self._grabber is not None:
            self._grabber.set_cancel_armed(active)

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
