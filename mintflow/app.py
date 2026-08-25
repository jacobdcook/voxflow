"""Platform-agnostic hold-to-talk orchestrator.

FlowApp talks to OS-specific code only through the Backend interface
(GTK/Xlib, tkinter/pynput, etc. stay in mintflow.platform).
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Callable, Protocol

import numpy as np

from mintflow.audio import Recorder
from mintflow.config import load_vocabulary, log, save_config
from mintflow.engine import Engine

STREAM_LOCK_S = 30.0
STREAM_TAIL_S = 10.0
LEVEL_MS = 40
LOAD_POLL_MS = 80
DONE_HIDE_MS = 380
ERROR_HIDE_MS = 900
RMS_SILENCE = 0.004


class Backend(Protocol):
    cfg: dict

    def run(self) -> None: ...
    def quit(self) -> None: ...
    def call_on_main(self, fn: Callable, *args: Any, **kwargs: Any): ...
    def call_later(self, ms: float, fn: Callable) -> Any: ...
    def call_later_s(self, s: float, fn: Callable) -> Any: ...
    def cancel(self, handle: Any) -> None: ...

    def overlay_show(self) -> None: ...
    def overlay_hide(self) -> None: ...
    def overlay_set_status(self, status: str, level: float = 0.0) -> None: ...
    def overlay_set_text(self, text: str) -> None: ...
    def overlay_place(self) -> None: ...

    def hotkey_grab(self, spec: str, on_press: Callable, on_release: Callable) -> None: ...
    def hotkey_ungrab(self) -> None: ...
    def hotkey_replay_tap(self) -> None: ...

    def paste_text(self, text: str, restore_ms: int) -> None: ...
    def focused_is_terminal(self) -> bool: ...

    def play_sound(self, kind: str) -> None: ...
    def capture_hotkey(self, timeout_s: float) -> dict | None: ...


def is_typing_key(hotkey_spec: str) -> bool:
    """True when a quick tap of the hotkey should type the character."""
    parts = [p.strip().lower() for p in str(hotkey_spec).split("+") if p.strip()]
    if not parts:
        return False
    key = parts[-1]
    if key.startswith("keycode:"):
        return False
    if key in {"shift", "ctrl", "control", "alt", "super", "win", "meta", "mod1", "cmd"}:
        return False
    return len(key) == 1 or key in {"space", "spacebar", "grave", "quoteleft", "apostrophe"}


def notify(body: str) -> None:
    log(body)
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(
                ["notify-send", "-a", "mintflow", "-u", "low", "mintflow", body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif sys.platform == "darwin":
            escaped = body.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    f'display notification "{escaped}" with title "mintflow"',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError:
        pass


def _join_transcript(prefix: str, tail: str) -> str:
    prefix = (prefix or "").strip()
    tail = (tail or "").strip()
    if not prefix:
        return tail
    if not tail:
        return prefix
    if tail.startswith(prefix):
        return tail
    return f"{prefix} {tail}"


class FlowApp:
    """Hold-to-talk state machine: idle, armed, listening, handsfree_stop,
    transcribing, cleaning.
    """

    def __init__(self, cfg: dict, backend: Backend | None = None) -> None:
        if backend is None:
            from mintflow.platform import get_backend

            backend = get_backend()
        if any(str(cfg.get(k, "")).lower() == "auto" for k in ("model", "device", "compute_type")):
            from mintflow.gpu import setup_auto_config

            cfg = setup_auto_config(cfg)
        self.cfg = cfg
        self.backend = backend
        self.backend.cfg = cfg
        self.recorder = Recorder(int(cfg["sample_rate"]))
        self.engine = Engine(cfg)
        self.state = "idle"
        self.handsfree = False
        if "hotkey_typing" in cfg:
            self.tap_through = bool(cfg["hotkey_typing"])
        else:
            self.tap_through = is_typing_key(str(cfg.get("hotkey") or "pause"))
        self._engine_lock = threading.Lock()
        self._level_src = None
        self._hf_src = None
        self._commit_handle = None
        self._held = False
        self._committed = False
        self._stream_stop = threading.Event()
        self._stream_thread: threading.Thread | None = None
        self._stream_confirmed = ""
        self._stream_confirmed_n = 0

    def start(self) -> None:
        threading.Thread(
            target=self.engine.preload, daemon=True, name="mintflow-preload"
        ).start()
        self.backend.hotkey_grab(
            str(self.cfg.get("hotkey") or "pause"),
            self._on_press,
            self._on_release,
        )
        self.backend.call_later(LOAD_POLL_MS, self._watch_load)
        log("starting")
        self.backend.run()

    def shutdown(self) -> None:
        self.state = "idle"
        self.handsfree = False
        self._stream_stop.set()
        self._cancel("_level_src")
        self._cancel("_hf_src")
        self._cancel("_commit_handle")
        try:
            self.backend.hotkey_ungrab()
        except Exception:
            pass
        try:
            self.recorder.stop()
        except Exception:
            pass
        try:
            self.backend.overlay_hide()
        except Exception:
            pass
        try:
            self.backend.quit()
        except Exception:
            pass

    def _watch_load(self) -> bool:
        if not self.engine.ready.is_set():
            return True
        if self.engine.model is None:
            notify("mintflow failed to load whisper. Check mintflow.log.")
            return False
        hk = self.cfg.get("hotkey_label") or str(self.cfg.get("hotkey") or "hotkey").upper()
        if self.tap_through:
            notify(f"mintflow is ready. Hold {hk} to talk.")
        else:
            notify(f"mintflow is ready. Hold {hk} to talk, tap {hk} for hands-free.")
        return False

    def _on_press(self) -> None:
        self._held = True
        self._committed = False
        self._cancel("_commit_handle")
        if self.state == "listening" and self.handsfree:
            self.state = "handsfree_stop"
            return
        self.arm()
        self._commit_handle = self.backend.call_later(
            int(self.cfg.get("tap_ms", 220)),
            self._commit_hold,
        )

    def _commit_hold(self) -> bool:
        self._commit_handle = None
        if not self._held or self._committed:
            return False
        self._committed = True
        self.reveal()
        return False

    def _on_release(self) -> None:
        self._held = False
        self._cancel("_commit_handle")
        committed = self._committed
        self._committed = False
        if committed:
            self.finish()
        else:
            self.abort_tap()

    def arm(self) -> bool:
        if self.state == "listening" and self.handsfree:
            self.state = "handsfree_stop"
            return False
        if self.state in ("transcribing", "cleaning", "listening", "armed", "handsfree_stop"):
            return False
        if not self.engine.ready.is_set():
            notify("mintflow is still loading the model")
            return False
        if self.engine.model is None:
            notify("mintflow failed to load whisper. Check mintflow.log.")
            return False
        self.state = "armed"
        try:
            self.recorder.start()
        except Exception as e:
            log(f"mic failed: {e}")
            self.state = "idle"
            notify(f"Microphone failed: {e}")
            return False
        return False

    def reveal(self) -> bool:
        if self.state != "armed":
            return False
        self.state = "listening"
        self._show_listening()
        return False

    def _show_listening(self) -> None:
        self.backend.overlay_place()
        self.backend.overlay_set_text("")
        self.backend.overlay_set_status("listening", 0.2)
        self.backend.overlay_show()
        self._sound("start")
        self._cancel("_level_src")
        self._level_src = self.backend.call_later(LEVEL_MS, self._pump_level)
        self._start_stream()

    def abort_tap(self) -> bool:
        if self.state == "handsfree_stop":
            return self._begin_processing()
        if self.state == "armed" and not self.tap_through:
            self.handsfree = True
            self.state = "listening"
            self._show_listening()
            self._cancel("_hf_src")
            self._hf_src = self.backend.call_later_s(
                int(self.cfg.get("handsfree_max_s", 180)),
                self._hf_timeout,
            )
            return False
        if self.state in ("armed", "listening"):
            self._stop_stream_flag()
            try:
                self.recorder.stop()
            except Exception:
                pass
            self.backend.overlay_hide()
            self.state = "idle"
            self.handsfree = False
        if self.tap_through:
            try:
                self.backend.hotkey_replay_tap()
            except Exception as e:
                log(f"tap replay: {e}")
        return False

    def _hf_timeout(self) -> bool:
        self._hf_src = None
        if self.state == "listening" and self.handsfree:
            self._begin_processing()
        return False

    def _pump_level(self) -> bool:
        if self.state != "listening":
            self._level_src = None
            return False
        self.backend.overlay_set_status("listening", self.recorder.level)
        return True

    def finish(self) -> bool:
        if self.state == "handsfree_stop":
            return self._begin_processing()
        if self.state == "armed":
            return self.abort_tap()
        if self.state != "listening" or self.handsfree:
            return False
        return self._begin_processing()

    def _begin_processing(self) -> bool:
        self._cancel("_hf_src")
        self._cancel("_level_src")
        self.handsfree = False
        self.state = "transcribing"
        self._stop_stream_flag()
        try:
            terminal = bool(self.backend.focused_is_terminal())
        except Exception as e:
            log(f"terminal detect: {e}")
            terminal = False
        try:
            audio = self.recorder.stop()
        except Exception as e:
            log(f"recorder stop: {e}")
            audio = np.zeros(0, dtype=np.float32)
        self.backend.overlay_set_text("")
        self.backend.overlay_set_status("transcribing")
        threading.Thread(
            target=self._process,
            args=(audio, terminal),
            daemon=True,
            name="mintflow-stt",
        ).start()
        return False

    def _process(self, audio: np.ndarray, terminal: bool = False) -> None:
        try:
            self._join_stream()
            sr = int(self.cfg["sample_rate"])
            min_s = float(self.cfg.get("min_seconds", 0.35))
            if audio is None or getattr(audio, "size", 0) < int(sr * min_s):
                self._ui(self._hide)
                return
            rms = float(np.sqrt(np.mean(np.square(audio))) + 1e-12)
            if rms < RMS_SILENCE:
                self._ui(self._hide)
                return
            vocab = load_vocabulary()
            t0 = time.monotonic()
            with self._engine_lock:
                raw = self.engine.transcribe(audio, fast=False, vocab_prompt=vocab)
            log(f"raw ({time.monotonic() - t0:.2f}s): {raw!r}")
            if not raw:
                self._ui(self._hide)
                return
            self._ui(self._enter_cleaning)
            text = self.engine.rewrite(raw, terminal)
            log(f"clean: {text!r}")
            if not text:
                self._ui(self._hide)
                return
            self.backend.paste_text(text, int(self.cfg.get("restore_clipboard_ms", 450)))
            self._sound("done")
            self._ui(self.backend.overlay_set_status, "done")
            self._ui_later(DONE_HIDE_MS, self._hide)
        except Exception:
            log("process failed:\n" + traceback.format_exc())
            self._sound("error")
            self._ui(self.backend.overlay_set_status, "error")
            self._ui_later(ERROR_HIDE_MS, self._hide)

    def _enter_cleaning(self) -> None:
        self.state = "cleaning"
        self.backend.overlay_set_status("cleaning")

    def _hide(self) -> bool:
        self.state = "idle"
        self.handsfree = False
        try:
            self.backend.overlay_hide()
        except Exception:
            pass
        return False

    def _start_stream(self) -> None:
        self._stop_stream_flag()
        self._join_stream(timeout=1.0)
        self._stream_confirmed = ""
        self._stream_confirmed_n = 0
        self._stream_stop = threading.Event()
        self._stream_thread = threading.Thread(
            target=self._stream_loop, daemon=True, name="mintflow-stream"
        )
        self._stream_thread.start()

    def _stop_stream_flag(self) -> None:
        self._stream_stop.set()

    def _join_stream(self, timeout: float = 20.0) -> None:
        t = self._stream_thread
        if t is None or not t.is_alive():
            self._stream_thread = None
            return
        if threading.current_thread() is t:
            return
        t.join(timeout=timeout)
        if not t.is_alive():
            self._stream_thread = None

    def _stream_loop(self) -> None:
        interval = float(self.cfg.get("stream_interval_s", 1.0))
        sr = int(self.cfg["sample_rate"])
        vocab = load_vocabulary()
        tail_n = max(1, int(STREAM_TAIL_S * sr))
        lock_n = max(tail_n + 1, int(STREAM_LOCK_S * sr))
        while not self._stream_stop.wait(interval):
            if self.state != "listening":
                break
            try:
                audio = self.recorder.get_snapshot()
            except Exception as e:
                log(f"stream snapshot: {e}")
                continue
            if audio is None or audio.size < int(sr * 0.6):
                continue
            try:
                text = self._stream_transcribe(audio, sr, vocab, tail_n, lock_n)
            except Exception:
                log("stream transcribe:\n" + traceback.format_exc())
                continue
            if text and self.state == "listening" and not self._stream_stop.is_set():
                self._ui(self._push_stream_text, text)

    def _stream_transcribe(
        self,
        audio: np.ndarray,
        sr: int,
        vocab: str,
        tail_n: int,
        lock_n: int,
    ) -> str:
        n = int(audio.size)
        with self._engine_lock:
            if self._stream_stop.is_set() or self.state != "listening":
                return ""
            if n <= lock_n:
                return self.engine.transcribe(audio, fast=True, vocab_prompt=vocab)
            if not self._stream_confirmed:
                head = audio[:-tail_n]
                self._stream_confirmed = self.engine.transcribe(
                    head, fast=True, vocab_prompt=vocab
                )
                self._stream_confirmed_n = n - tail_n
            unlocked = n - self._stream_confirmed_n
            grow_n = int(STREAM_TAIL_S * 1.5 * sr)
            if unlocked > grow_n and self._stream_confirmed_n > 0:
                new_end = n - tail_n
                chunk = audio[self._stream_confirmed_n : new_end]
                if chunk.size >= int(sr * 0.4):
                    extra = self.engine.transcribe(chunk, fast=True, vocab_prompt=vocab)
                    if extra:
                        self._stream_confirmed = _join_transcript(
                            self._stream_confirmed, extra
                        )
                    self._stream_confirmed_n = new_end
            tail = audio[self._stream_confirmed_n :]
            tail_text = self.engine.transcribe(tail, fast=True, vocab_prompt=vocab)
        return _join_transcript(self._stream_confirmed, tail_text)

    def _push_stream_text(self, text: str) -> None:
        if self.state == "listening" and text:
            self.backend.overlay_set_text(text)

    def _sound(self, kind: str) -> None:
        if not self.cfg.get("sounds", True):
            return
        try:
            self.backend.play_sound(kind)
        except Exception as e:
            log(f"sound: {e}")

    def _ui(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        try:
            self.backend.call_on_main(fn, *args, **kwargs)
        except Exception as e:
            log(f"ui: {e}")

    def _ui_later(self, ms: int, fn: Callable) -> None:
        def _go() -> None:
            self.backend.call_later(ms, fn)

        self._ui(_go)

    def _cancel(self, attr: str) -> None:
        handle = getattr(self, attr, None)
        if not handle:
            return
        try:
            self.backend.cancel(handle)
        except Exception:
            pass
        setattr(self, attr, None)


def apply_captured_hotkey(cfg: dict, captured: dict) -> dict:
    cfg = dict(cfg)
    cfg["hotkey"] = captured["spec"]
    if captured.get("label"):
        cfg["hotkey_label"] = captured["label"]
    if "typing" in captured:
        cfg["hotkey_typing"] = bool(captured["typing"])
    save_config(cfg)
    return cfg
