"""Drive FlowApp against a fake backend that mimics the Linux repeat_ms debounce."""
import os
import sys
import tempfile
import threading
import time

import numpy as np

# Keep the app's log()/notify() calls out of the user's real config dir when
# this file is run directly (run_all.py also sets this for every suite).
if "VOXFLOW_TEST_CFG" not in os.environ:
    os.environ["VOXFLOW_TEST_CFG"] = tempfile.mkdtemp(prefix="voxflow-tests-")
    os.environ["XDG_CONFIG_HOME"] = os.environ["VOXFLOW_TEST_CFG"]

import voxflow.app as A

CLOCK = {"now": 1000.0}


class FakeTime:
    """app.py only reads time.monotonic; drive it from the fake timer wheel."""

    @staticmethod
    def monotonic():
        return CLOCK["now"]


A.time = FakeTime


class FakeRecorder:
    def __init__(self, sr):
        self.sample_rate = sr
        self.level = 0.0
        self.started = 0
        self.stopped = 0
        self.seconds = 1.0

    def start(self):
        self.started += 1

    def _buf(self):
        n = int(self.sample_rate * self.seconds)
        return np.full(n, 0.05, dtype=np.float32)

    def get_snapshot(self):
        return self._buf()

    def stop(self):
        self.stopped += 1
        return self._buf()


class FakeEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = object()
        self.ready = threading.Event()
        self.ready.set()
        self.error = None
        self.calls = []

    def preload(self):
        pass

    def transcribe(self, audio, fast=False, vocab_prompt=""):
        self.calls.append(("t", fast, int(getattr(audio, "size", 0))))
        return "hello world"

    def rewrite(self, text, terminal=False):
        return "Hello world."


class FakeBackend:
    """Runs callbacks inline; timers fire only when advance() is called."""

    def __init__(self):
        self.cfg = {}
        self.timers = {}
        self._next = 1
        self.now = 0.0
        self.events = []
        self.pasted = []

    def run(self):
        pass

    def quit(self):
        self.events.append("quit")

    def call_on_main(self, fn, *a, **k):
        fn(*a, **k)

    def call_later(self, ms, fn):
        h = self._next
        self._next += 1
        self.timers[h] = (self.now + ms / 1000.0, fn)
        return h

    def call_later_s(self, s, fn):
        return self.call_later(float(s) * 1000.0, fn)

    def cancel(self, handle):
        self.timers.pop(handle, None)

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = [(t, h) for h, (t, _f) in self.timers.items() if t <= end]
            if not due:
                break
            due.sort()
            t, h = due[0]
            entry = self.timers.pop(h, None)
            if entry is None:
                continue
            self.now = t
            CLOCK["now"] = t
            entry[1]()
        self.now = end
        CLOCK["now"] = end

    def overlay_show(self):
        self.events.append("show")

    def overlay_hide(self):
        self.events.append("hide")

    def overlay_set_status(self, s, level=0.0):
        self.events.append(f"status:{s}")

    def overlay_set_text(self, t):
        pass

    def overlay_place(self):
        pass

    def hotkey_grab(self, spec, on_press, on_release):
        self.on_press = on_press
        self.on_release = on_release

    def hotkey_ungrab(self):
        pass

    def hotkey_replay_tap(self):
        self.events.append("replay")

    def paste_text(self, text, restore_ms):
        self.pasted.append(text)

    def focused_is_terminal(self):
        return False

    def play_sound(self, kind):
        pass

    def capture_hotkey(self, t):
        return None


def make(hotkey="pause", **over):
    cfg = {
        "hotkey": hotkey, "handsfree_max_s": 180, "model": "small", "device": "cpu",
        "compute_type": "int8", "language": "en", "style": "casual", "cleanup": "local",
        "ollama_model": "x", "ollama_url": "http://127.0.0.1:11434", "sample_rate": 16000,
        "tap_ms": 220, "repeat_ms": 80, "min_seconds": 0.35, "sounds": False,
        "restore_clipboard_ms": 450, "stream_interval_s": 1.0,
    }
    cfg.update(over)
    b = FakeBackend()
    app = A.FlowApp.__new__(A.FlowApp)
    app.cfg = cfg
    app.backend = b
    b.cfg = cfg
    app.recorder = FakeRecorder(16000)
    app.engine = FakeEngine(cfg)
    app.state = "idle"
    app.handsfree = False
    app.tap_through = A.is_typing_key(hotkey)
    app._engine_lock = threading.Lock()
    app._level_src = None
    app._hf_src = None
    app._max_src = None
    app._commit_handle = None
    app._held = False
    app._committed = False
    app._press_t = 0.0
    app._busy_notified = 0.0
    app._stream_stop = threading.Event()
    app._stream_stop.set()
    app._stream_thread = None
    app._stream_confirmed = ""
    app._stream_confirmed_n = 0
    if hasattr(app, "_press_t"):
        app._press_t = 0.0
    b.hotkey_grab(hotkey, app._on_press, app._on_release)
    return app, b


def press_release(app, b, hold_s, repeat_ms=0.080):
    """Mimic linux HotkeyGrabber: release callback delayed by repeat_ms."""
    b.on_press()
    b.advance(hold_s)
    b.advance(repeat_ms)
    b.on_release()


def settle(app, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        alive = [
            t for t in threading.enumerate() if t.name in ("voxflow-stt", "voxflow-stream")
        ]
        if not alive:
            return
        time.sleep(0.02)


fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


# 1. short tap on a non-typing key must toggle hands-free
app, b = make("pause")
press_release(app, b, 0.150)
check(
    "tap 150ms on pause -> handsfree",
    app.handsfree and app.state == "listening",
    f"state={app.state} handsfree={app.handsfree}",
)

# 2. very short tap works
app, b = make("pause")
press_release(app, b, 0.050)
check(
    "tap 50ms on pause -> handsfree",
    app.handsfree and app.state == "listening",
    f"state={app.state} handsfree={app.handsfree}",
)

# 3. real hold -> processes and pastes
app, b = make("pause")
b.on_press()
b.advance(1.0)
b.on_release()
settle(app)
b.advance(1.0)
check("hold 1s -> paste", b.pasted == ["Hello world."], f"pasted={b.pasted}")

# 4. typing key tap replays the character
app, b = make("grave")
press_release(app, b, 0.100)
check("tap on typing key -> replay", "replay" in b.events, f"events={b.events}")

# 5. rapid tap sequence must not leave a stuck state
app, b = make("pause")
for _ in range(6):
    press_release(app, b, 0.040)
settle(app)
b.advance(2.0)
check(
    "6 rapid taps -> settles idle or listening",
    app.state in ("idle", "listening"),
    f"state={app.state}",
)

# 6. hotkey pressed while transcribing must not start a phantom recording
app, b = make("pause")
app.state = "transcribing"
started = app.recorder.started
b.on_press()
b.advance(0.5)
b.on_release()
check(
    "press while transcribing -> no recorder start",
    app.recorder.started == started,
    f"started={app.recorder.started}",
)

# 7. empty audio -> hide, no paste
app, b = make("pause")
app.recorder.seconds = 0.0
b.on_press()
b.advance(1.0)
b.on_release()
settle(app)
b.advance(1.0)
check("empty audio -> no paste", b.pasted == [], f"pasted={b.pasted}")

# 8. handsfree auto-stop after handsfree_max_s
app, b = make("pause", handsfree_max_s=5)
press_release(app, b, 0.050)
b.advance(6.0)
settle(app)
b.advance(1.0)
check("handsfree timeout -> paste", b.pasted == ["Hello world."], f"pasted={b.pasted}")

# 9. handsfree second tap stops it
app, b = make("pause")
press_release(app, b, 0.050)
press_release(app, b, 0.050)
settle(app)
b.advance(1.0)
check("handsfree tap again -> paste", b.pasted == ["Hello world."], f"pasted={b.pasted}")

# 10. long recording: hold well past handsfree_max_s still finishes
app, b = make("pause")
app.recorder.seconds = 400.0
b.on_press()
b.advance(400.0)
b.on_release()
settle(app, timeout=10)
b.advance(1.0)
check("400s hold -> paste", b.pasted == ["Hello world."], f"pasted={b.pasted}")

# 11. holding past max_seconds must auto-stop instead of recording forever
app, b = make("pause", max_seconds=10, handsfree_max_s=5)
app.recorder.seconds = 12.0
b.on_press()
b.advance(12.0)
settle(app)
b.advance(1.0)
check("max_seconds hard stop while held", b.pasted == ["Hello world."], f"pasted={b.pasted}")
b.on_release()
b.advance(1.0)
check("release after hard stop -> idle", app.state == "idle", f"state={app.state}")

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
