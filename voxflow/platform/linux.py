"""Linux/X11 backend: Xlib hotkey grab, GTK3 overlay, xclip paste."""

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
    from Xlib import XK, X, display as xdisplay
    from Xlib.ext import xtest
except ImportError as e:
    raise BackendUnavailable(
        "The hotkey needs python-xlib, which is not installed.\n"
        "  Ubuntu / Debian / Mint:  sudo apt install python3-xlib\n"
        "  Anything else:           pip install python-xlib"
    ) from e

try:
    import cairo
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo
except (ImportError, ValueError) as e:
    raise BackendUnavailable(
        "The overlay needs GTK 3, which is not installed.\n"
        "  Ubuntu / Debian / Mint:  sudo apt install python3-gi python3-gi-cairo "
        "gir1.2-gtk-3.0\n"
        "  Fedora:                  sudo dnf install python3-gobject gtk3"
    ) from e

TERMINAL_CLASSES = {
    "gnome-terminal",
    "gnome-terminal-server",
    "alacritty",
    "kitty",
    "xfce4-terminal",
    "terminator",
    "xterm",
    "urxvt",
    "konsole",
    "tilix",
    "guake",
    "ptyxis",
    "org.gnome.terminal",
    "org.gnome.console",
    "kgx",
    "cool-retro-term",
    "wezterm",
    "foot",
    "st",
    "ghostty",
    "com.mitchellh.ghostty",
}

MOD_MAP = {
    "shift": X.ShiftMask,
    "ctrl": X.ControlMask,
    "control": X.ControlMask,
    "alt": X.Mod1Mask,
    "mod1": X.Mod1Mask,
    "super": X.Mod4Mask,
    "win": X.Mod4Mask,
    "meta": X.Mod4Mask,
}

EXTRA_MASKS = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]

MODIFIER_KEYNAMES = {
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Alt_L",
    "Alt_R",
    "Super_L",
    "Super_R",
    "Meta_L",
    "Meta_R",
    "ISO_Level3_Shift",
    "Caps_Lock",
    "Num_Lock",
}

SOUND_FILES = {
    "start": "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
    "done": "/usr/share/sounds/freedesktop/stereo/message.oga",
    "error": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
}

# xclip/xprop/xdotool can wedge if the X server or a clipboard owner stalls.
CMD_TIMEOUT_S = 3.0
_INJECT_LOCK = threading.Lock()

COMPACT_W = 280
COMPACT_H = 64
EXPANDED_W = 500
HEADER_H = 64
TEXT_MAX_W = 460
TEXT_MAX_LINES = 3
TEXT_LINE_H = 18
BOTTOM_MARGIN = 72

STATUS_LABELS = {
    "listening": "Listening",
    "transcribing": "Transcribing",
    "cleaning": "Cleaning up",
    "done": "Done",
    "error": "Try again",
    "cancelled": "Cancelled",
    "loading": "Loading",
}


def parse_hotkey(spec: str) -> tuple[int, str]:
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    key = parts[-1]
    mask = 0
    for part in parts[:-1]:
        if part not in MOD_MAP:
            raise ValueError(f"unknown modifier {part}")
        mask |= MOD_MAP[part]
    return mask, key


def is_typing_key(hotkey_spec: str) -> bool:
    """True when the hotkey's base key produces a character when tapped."""
    _mask, key = parse_hotkey(hotkey_spec)
    if key.startswith("keycode:"):
        return False
    return len(key) == 1 or key == "space"


def cairo_empty_region():
    return cairo.Region(cairo.RectangleInt(0, 0, 0, 0))


def _rounded(cr, x, y, w, h, r) -> None:
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.57, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.57)
    cr.arc(x + r, y + h - r, r, 1.57, 3.14)
    cr.arc(x + r, y + r, r, 3.14, 4.71)
    cr.close_path()


def _schedule(fn) -> None:
    if not fn:
        return

    def _go():
        try:
            fn()
        except Exception as e:
            log(f"callback: {e}")
        return False

    GLib.idle_add(_go)


def _wrap_timer(fn):
    def _go(*_args):
        try:
            return bool(fn())
        except Exception as e:
            log(f"timer: {e}")
            return False

    return _go


# ---------------------------------------------------------------------------
# Overlay (GTK3 POPUP, Cairo waveform, PangoCairo streaming text)
# ---------------------------------------------------------------------------


class Overlay(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.stick()

        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.status = "listening"
        self.level = 0.0
        self._text = ""
        self._bars = [0.12] * 18
        self._t0 = time.monotonic()
        self._win_w = COMPACT_W
        self._win_h = COMPACT_H
        self._tick_src = None
        self.set_size_request(COMPACT_W, COMPACT_H)
        self.connect("draw", self._on_draw)
        self.connect("realize", self._on_realize)

    def start_animation(self) -> None:
        # voxflow sits idle most of the day; a 30 Hz redraw timer that never
        # stops is pure battery drain on a laptop.
        if self._tick_src is None:
            self._tick_src = GLib.timeout_add(33, self._tick)

    def stop_animation(self) -> None:
        if self._tick_src is not None:
            try:
                GLib.source_remove(self._tick_src)
            except Exception:
                pass
            self._tick_src = None

    def _on_realize(self, *_args) -> None:
        gdk_win = self.get_window()
        if gdk_win is None:
            return
        try:
            gdk_win.set_override_redirect(True)
            gdk_win.input_shape_combine_region(cairo_empty_region(), 0, 0)
        except Exception as e:
            log(f"overlay realize: {e}")

    def _expanded(self) -> bool:
        return bool(self._text.strip()) and self.status in (
            "listening",
            "transcribing",
            "cleaning",
            "done",
        )

    def _measure_text_h(self, text: str) -> int:
        layout = self.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription("Noto Sans 13"))
        layout.set_width(TEXT_MAX_W * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        _w, h = layout.get_pixel_size()
        return h

    def _sync_size(self) -> None:
        if self._expanded():
            vis_h = min(self._measure_text_h(self._text), TEXT_MAX_LINES * TEXT_LINE_H + 4)
            w = EXPANDED_W
            h = max(120, min(180, HEADER_H + 10 + vis_h + 16))
        else:
            w, h = COMPACT_W, COMPACT_H
        if (w, h) == (self._win_w, self._win_h):
            return
        self._win_w, self._win_h = w, h
        self.set_size_request(w, h)
        self.resize(w, h)
        if self.get_visible():
            self.place_on_pointer_monitor()

    def place_on_pointer_monitor(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        monitor = None
        # The text lands in the focused window, so put the overlay on that
        # monitor. The pointer can easily be parked on a different screen.
        try:
            screen = Gdk.Screen.get_default()
            active = screen.get_active_window() if screen else None
            if active is not None:
                monitor = display.get_monitor_at_window(active)
        except Exception:
            monitor = None
        if monitor is None:
            seat = display.get_default_seat()
            ptr = seat.get_pointer() if seat else None
            x = y = 0
            if ptr is not None:
                _, x, y = ptr.get_position()
            monitor = display.get_monitor_at_point(x, y)
        if monitor is None:
            monitor = display.get_primary_monitor()
        if monitor is None:
            return
        try:
            geo = monitor.get_workarea()  # keeps clear of panels and docks
        except Exception:
            geo = monitor.get_geometry()
        w, h = self._win_w, self._win_h
        margin = min(BOTTOM_MARGIN, max(0, geo.height - h))
        self.move(geo.x + (geo.width - w) // 2, geo.y + geo.height - h - margin)

    def set_status(self, status: str, level: float | None = None) -> None:
        self.status = status
        if level is not None:
            self.level = max(0.0, min(1.0, level))
        self._sync_size()
        self.queue_draw()

    def set_text(self, text: str) -> None:
        self._text = text or ""
        self._sync_size()
        self.queue_draw()

    def reset(self) -> None:
        self._text = ""
        self.status = "listening"
        self.level = 0.0
        self._sync_size()
        self.queue_draw()

    def _tick(self) -> bool:
        if not self.get_visible():
            self._tick_src = None
            return False
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
        self.queue_draw()
        return True

    def _bar_color(self):
        if self.status == "listening":
            return (0.541, 0.847, 1.0, 0.95)  # #8AD8FF
        if self.status == "cleaning":
            return (1.0, 0.851, 0.302, 0.95)  # #FFD94D
        if self.status == "done":
            return (0.553, 0.922, 0.624, 0.95)  # #8DEB9F
        if self.status == "error":
            return (1.0, 0.451, 0.451, 0.95)  # #FF7373
        if self.status == "cancelled":
            return (0.7, 0.7, 0.7, 0.9)
        return (1.0, 1.0, 1.0, 0.88)

    def _on_draw(self, _widget, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        expanded = self._expanded()
        radius = (COMPACT_H / 2) if not expanded else 20

        cr.set_source_rgba(0.071, 0.071, 0.078, 0.92)  # #121214
        _rounded(cr, 0, 0, w, h, radius)
        cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.08)
        _rounded(cr, 0.5, 0.5, w - 1, h - 1, radius)
        cr.set_line_width(1)
        cr.stroke()

        label = STATUS_LABELS.get(self.status, self.status)
        cr.select_font_face("Noto Sans", 0, 0)
        cr.set_font_size(12)
        cr.set_source_rgba(1, 1, 1, 0.55)
        ext = cr.text_extents(label)
        cr.move_to(22, 18 + ext.height / 2)
        cr.show_text(label)

        n = len(self._bars)
        gap = 3
        bar_w = 6
        total = n * bar_w + (n - 1) * gap
        x0 = (w - total) / 2
        y_mid = HEADER_H * 0.62
        max_h = HEADER_H * 0.42
        cr.set_source_rgba(*self._bar_color())
        for i, b in enumerate(self._bars):
            bh = max(4, b * max_h)
            x = x0 + i * (bar_w + gap)
            _rounded(cr, x, y_mid - bh / 2, bar_w, bh, 2)
            cr.fill()

        if expanded:
            self._draw_text(cr)

        return False

    def _draw_text(self, cr) -> None:
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Noto Sans 13"))
        layout.set_width(TEXT_MAX_W * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        shown = self._text + " ▍" if self.status == "listening" else self._text
        layout.set_text(shown, -1)
        _pw, ph = layout.get_pixel_size()
        max_h = TEXT_MAX_LINES * TEXT_LINE_H + 4
        text_x, text_y = 20, HEADER_H + 4
        cr.save()
        cr.rectangle(text_x, text_y, TEXT_MAX_W, max_h)
        cr.clip()
        y_off = min(0, max_h - ph)
        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.move_to(text_x, text_y + y_off)
        PangoCairo.show_layout(cr, layout)
        cr.restore()


# ---------------------------------------------------------------------------
# Clipboard / paste / terminal / sound
# ---------------------------------------------------------------------------


def active_window_id() -> str:
    try:
        return subprocess.check_output(
            ["xdotool", "getactivewindow"],
            text=True,
            timeout=CMD_TIMEOUT_S,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def focused_is_terminal() -> bool:
    wid = active_window_id()
    if not wid:
        return False
    try:
        raw = subprocess.check_output(
            ["xprop", "-id", wid, "WM_CLASS"],
            text=True,
            timeout=CMD_TIMEOUT_S,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return any(c.lower() in raw.lower() for c in TERMINAL_CLASSES)


def clipboard_get() -> bytes:
    try:
        return subprocess.check_output(
            ["xclip", "-selection", "clipboard", "-o"],
            stderr=subprocess.DEVNULL,
            timeout=CMD_TIMEOUT_S,
        )
    except Exception:
        return b""


def clipboard_set_bytes(data: bytes) -> None:
    p = subprocess.Popen(["xclip", "-selection", "clipboard", "-i"], stdin=subprocess.PIPE)
    try:
        p.communicate(data, timeout=CMD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        log("xclip timed out while setting the clipboard")


def clipboard_set(text: str) -> None:
    clipboard_set_bytes(text.encode("utf-8"))


_last_inject: dict = {"payload": None, "old": b""}


def inject_text(text: str, restore_ms: int) -> None:
    if not text:
        return
    payload = text.encode("utf-8")
    with _INJECT_LOCK:
        old = clipboard_get()
        # Two dictations inside one restore window: the clipboard still holds
        # our previous paste, so the user's real clipboard is the one we saved
        # last time, not the payload we are about to overwrite.
        if _last_inject["payload"] is not None and old == _last_inject["payload"]:
            old = _last_inject["old"]
        _last_inject["payload"] = payload
        _last_inject["old"] = old
        clipboard_set_bytes(payload)
        time.sleep(0.04)
        combo = "ctrl+shift+v" if focused_is_terminal() else "ctrl+v"
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", combo],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CMD_TIMEOUT_S,
        )

    def restore():
        time.sleep(max(0, restore_ms) / 1000)
        if not old:
            # Nothing (or something we cannot represent as text) was there
            # before. Clearing the clipboard would be worse than leaving ours.
            return
        with _INJECT_LOCK:
            # Only undo our own paste. A newer dictation or a manual copy in the
            # meantime must win, otherwise we clobber the user's clipboard.
            if clipboard_get() != payload:
                return
            clipboard_set_bytes(old)

    threading.Thread(target=restore, daemon=True, name="voxflow-clip").start()


def play_sound_file(kind: str, volume: float = 0.3) -> None:
    path = SOUND_FILES.get(kind)
    if not path or not os.path.exists(path):
        return
    level = int(max(0.0, min(1.0, volume)) * 65536)
    if level <= 0:
        return
    try:
        subprocess.Popen(
            ["paplay", "--volume", str(level), path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        log(f"paplay: {e}")


# ---------------------------------------------------------------------------
# X11 hold-to-talk (GrabModeAsync, autorepeat debounce, tap vs hold)
# ---------------------------------------------------------------------------


class HotkeyGrabber(threading.Thread):
    def __init__(
        self,
        spec: str,
        on_press=None,
        on_release=None,
        tap_ms: int = 220,
        repeat_ms: int = 80,
        on_arm=None,
        on_commit=None,
        on_end=None,
        on_tap=None,
    ) -> None:
        super().__init__(daemon=True, name="voxflow-hotkey")
        self.spec = spec
        self.on_arm = on_arm or on_press
        self.on_commit = on_commit
        self.on_end = on_end or on_release
        self.on_tap = on_tap or on_release
        self.on_cancel = None
        self.tap_ms = tap_ms
        self.repeat_ms = repeat_ms
        self._stop = threading.Event()
        # Xlib is not thread-safe: the grabber thread polls events while the
        # main thread may call ungrab()/replay_tap()/stop(). Every dpy access
        # goes through this lock.
        self._x_lock = threading.RLock()
        # Guards _held/_committed and the two debounce timers, which are
        # touched by the grabber thread and by timer threads.
        self._state_lock = threading.RLock()
        self.dpy = xdisplay.Display()
        self.root = self.dpy.screen().root
        self.mask, self.keyname = parse_hotkey(spec)
        self.keycode = self._keycode(self.keyname)
        if not self.keycode:
            raise RuntimeError(f"cannot resolve key {self.keyname}")
        self._held = False
        self._committed = False
        self._stop_timer: threading.Timer | None = None
        self._commit_timer: threading.Timer | None = None
        # Cancel keys (Escape / End) are grabbed only while a recording is live.
        self.cancel_keycodes: set[int] = set()
        for name in ("Escape", "End"):
            ks = XK.string_to_keysym(name)
            kc = self.dpy.keysym_to_keycode(ks) if ks else 0
            if kc:
                self.cancel_keycodes.add(kc)
        self._cancel_want = False
        self._cancel_grabbed = False

    def _keycode(self, name: str) -> int:
        if name.startswith("keycode:"):
            try:
                return int(name.split(":", 1)[1])
            except ValueError:
                return 0
        aliases = {
            "v": "v",
            "space": "space",
            "pause": "Pause",
            "break": "Pause",
            "scrolllock": "Scroll_Lock",
            "scroll_lock": "Scroll_Lock",
            "print": "Print",
            "menu": "Menu",
            "insert": "Insert",
            "kp_insert": "KP_Insert",
            "kp_enter": "KP_Enter",
            "kp_plus": "KP_Add",
            "kp_minus": "KP_Subtract",
        }
        sym_name = aliases.get(name, name)
        candidates = [sym_name]
        if re.fullmatch(r"f\d{1,2}", name):
            candidates.insert(0, name.upper())
        candidates += [sym_name.capitalize(), sym_name.upper()]
        for cand in candidates:
            ks = XK.string_to_keysym(cand)
            if not ks:
                ks = getattr(XK, f"XK_{cand}", 0)
            if ks:
                kc = self.dpy.keysym_to_keycode(ks)
                if kc:
                    return kc
        return 0

    def _all_masks(self) -> list[int]:
        return [self.mask | extra for extra in EXTRA_MASKS]

    def grab(self) -> None:
        with self._x_lock:
            for m in self._all_masks():
                self.root.grab_key(
                    self.keycode, m, True, X.GrabModeAsync, X.GrabModeAsync
                )
            self.dpy.flush()
        log(f"grabbed {self.spec}")

    def ungrab(self) -> None:
        with self._x_lock:
            for m in self._all_masks():
                self.root.ungrab_key(self.keycode, m)
            self.dpy.flush()

    def replay_tap(self) -> None:
        with self._x_lock:
            self.ungrab()
            try:
                xtest.fake_input(self.dpy, X.KeyPress, self.keycode)
                xtest.fake_input(self.dpy, X.KeyRelease, self.keycode)
                self.dpy.flush()
            finally:
                # Never leave the hotkey ungrabbed because the fake tap failed.
                self.grab()

    def stop(self) -> None:
        self._stop.set()
        with self._state_lock:
            self._cancel_stop_timer()
            self._cancel_commit_timer()
        try:
            self.ungrab()
        except Exception:
            pass
        try:
            with self._x_lock:
                if self._cancel_grabbed:
                    for kc in self.cancel_keycodes:
                        for m in EXTRA_MASKS:
                            self.root.ungrab_key(kc, m)
                    self._cancel_grabbed = False
        except Exception:
            pass
        try:
            with self._x_lock:
                self.dpy.flush()
        except Exception:
            pass

    def _cancel_stop_timer(self) -> None:
        with self._state_lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None

    def _cancel_commit_timer(self) -> None:
        with self._state_lock:
            if self._commit_timer is not None:
                self._commit_timer.cancel()
                self._commit_timer = None

    def _commit_hold(self) -> None:
        with self._state_lock:
            self._commit_timer = None
            if not self._held or self._committed:
                return
            self._committed = True
        if self.on_commit:
            _schedule(self.on_commit)

    def _finish_release(self) -> None:
        with self._state_lock:
            self._stop_timer = None
            if not self._held:
                return
            self._held = False
            self._cancel_commit_timer()
            committed = self._committed
            self._committed = False
        if committed:
            _schedule(self.on_end)
        else:
            _schedule(self.on_tap)

    def set_cancel_armed(self, active: bool) -> None:
        """Thread-safe request; the grabber thread applies it on its next tick."""
        self._cancel_want = bool(active)

    def _sync_cancel_grab(self) -> None:
        if self._cancel_want == self._cancel_grabbed:
            return
        try:
            with self._x_lock:
                if self._cancel_want:
                    for kc in self.cancel_keycodes:
                        for m in EXTRA_MASKS:
                            self.root.grab_key(
                                kc, m, True, X.GrabModeAsync, X.GrabModeAsync
                            )
                else:
                    for kc in self.cancel_keycodes:
                        for m in EXTRA_MASKS:
                            self.root.ungrab_key(kc, m)
                self.dpy.flush()
            self._cancel_grabbed = self._cancel_want
        except Exception as e:
            log(f"cancel grab: {e}")
            self._cancel_grabbed = self._cancel_want

    def run(self) -> None:
        try:
            self.grab()
            with self._x_lock:
                self.root.change_attributes(
                    event_mask=X.KeyPressMask | X.KeyReleaseMask
                )
        except Exception as e:
            # Another client already grabbed this key (BadAccess) or the
            # display is unusable. Surface it instead of dying silently.
            log(f"hotkey grab failed for {self.spec}: {e}")
            try:
                subprocess.run(
                    [
                        "notify-send",
                        "voxflow",
                        f"Could not grab hotkey {self.spec}: already in use?",
                    ],
                    check=False,
                    timeout=5,
                )
            except Exception:
                pass
            return
        while not self._stop.is_set():
            self._sync_cancel_grab()
            with self._x_lock:
                pending = self.dpy.pending_events()
            if pending == 0:
                time.sleep(0.008)
                continue
            with self._x_lock:
                ev = self.dpy.next_event()
            if ev.type == X.MappingNotify:
                with self._x_lock:
                    self.dpy.refresh_keyboard_mapping(ev)
                continue
            if (
                ev.type == X.KeyPress
                and self._cancel_grabbed
                and getattr(ev, "detail", None) in self.cancel_keycodes
            ):
                if self.on_cancel:
                    _schedule(self.on_cancel)
                continue
            if ev.type == X.KeyPress and getattr(ev, "detail", None) == self.keycode:
                with self._state_lock:
                    self._cancel_stop_timer()
                    if self._held:
                        continue
                    self._held = True
                    self._committed = False
                    self._commit_timer = threading.Timer(
                        self.tap_ms / 1000, self._commit_hold
                    )
                    self._commit_timer.daemon = True
                    self._commit_timer.start()
                _schedule(self.on_arm)
            elif ev.type == X.KeyRelease and getattr(ev, "detail", None) == self.keycode:
                with self._state_lock:
                    self._cancel_stop_timer()
                    self._stop_timer = threading.Timer(
                        self.repeat_ms / 1000, self._finish_release
                    )
                    self._stop_timer.daemon = True
                    self._stop_timer.start()


# ---------------------------------------------------------------------------
# Set-hotkey dialog
# ---------------------------------------------------------------------------


def capture_hotkey_dialog(timeout_s: float = 15) -> dict | None:
    captured: dict = {}
    loop = GLib.MainLoop()

    win = Gtk.Window(title="voxflow hotkey")
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_keep_above(True)
    win.set_modal(True)
    win.set_default_size(460, 160)
    label = Gtk.Label()
    label.set_justify(Gtk.Justification.CENTER)
    win.add(label)

    remaining = {"n": int(timeout_s)}

    def render() -> None:
        label.set_markup(
            "<span size='xx-large' weight='bold'>Press the key you want for voxflow</span>\n"
            "<span size='large'>Any key or combo. Esc cancels.</span>\n"
            f"<span size='small' alpha='60%'>{remaining['n']}s left</span>"
        )

    def countdown() -> bool:
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            loop.quit()
            return False
        render()
        return True

    def on_key(_w, ev) -> bool:
        name = Gdk.keyval_name(ev.keyval) or ""
        if name == "Escape":
            loop.quit()
            return True
        if name in MODIFIER_KEYNAMES:
            return True
        mods = []
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            mods.append("ctrl")
        if ev.state & Gdk.ModifierType.MOD1_MASK:
            mods.append("alt")
        if ev.state & Gdk.ModifierType.SUPER_MASK:
            mods.append("super")
        if ev.state & Gdk.ModifierType.SHIFT_MASK:
            mods.append("shift")
        uni = Gdk.keyval_to_unicode(ev.keyval)
        typing = bool(uni and chr(uni).strip())
        if len(name) == 1 and name.isalnum():
            base = name.lower()
        elif name.lower() in ("pause", "break"):
            base = "pause"
        elif re.fullmatch(r"F\d{1,2}", name or "", re.I):
            base = name.lower()
        elif name.lower() in ("space", "spacebar"):
            base = "space"
        else:
            base = f"keycode:{ev.hardware_keycode}"
        captured["spec"] = "+".join(mods + [base])
        captured["label"] = "+".join([m.upper() for m in mods] + [name.upper()])
        captured["typing"] = typing
        loop.quit()
        return True

    render()
    win.connect("key-press-event", on_key)
    win.connect("destroy", lambda *_: loop.quit())
    win.show_all()
    win.present()
    timer_id = GLib.timeout_add_seconds(1, countdown)
    try:
        loop.run()
    finally:
        try:
            GLib.source_remove(timer_id)
        except Exception:
            pass
        win.destroy()

    if "spec" not in captured:
        return None
    return captured


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class LinuxBackend:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or {}
        self._overlay = Overlay()
        self._grabber: HotkeyGrabber | None = None

    def run(self) -> None:
        Gtk.main()

    def quit(self) -> None:
        self.hotkey_ungrab()
        Gtk.main_quit()

    def call_on_main(self, fn, *args, **kwargs):
        def _go(*_ignored):
            try:
                result = fn(*args, **kwargs)
                return bool(result)
            except Exception as e:
                log(f"call_on_main: {e}")
                return False

        return GLib.idle_add(_go)

    def call_later(self, ms, fn):
        return GLib.timeout_add(max(1, int(ms)), _wrap_timer(fn))

    def call_later_s(self, s, fn):
        return self.call_later(max(1, int(float(s) * 1000)), fn)

    def cancel(self, handle) -> None:
        if not handle:
            return
        try:
            GLib.source_remove(handle)
        except Exception:
            pass

    def overlay_show(self) -> None:
        self._overlay.show_all()
        self._overlay.start_animation()

    def overlay_hide(self) -> None:
        self._overlay.stop_animation()
        self._overlay.hide()
        self._overlay.reset()

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
        return capture_hotkey_dialog(timeout_s)
