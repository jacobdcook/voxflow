"""mintflow command-line entry point."""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from mintflow.config import (
    CONFIG_PATH,
    PID_PATH,
    VOCAB_PATH,
    load_config,
    load_vocabulary,
    log,
)

HELP = """mintflow: hold a key, speak, release. Cleaned text pastes into the focused app.
  mintflow              start daemon
  mintflow run          start daemon
  mintflow quit         stop daemon
  mintflow stop         stop daemon
  mintflow setup        detect GPU, set hotkey, pull Ollama model
  mintflow set-hotkey   press a key, it becomes the hotkey
  mintflow test-mic     record 2.5s and transcribe
  mintflow test-inject  paste a test string
  mintflow demo         show overlay animation
  mintflow models       print GPU detection and recommended model
  mintflow help         this message
  config: {config}
  vocabulary: {vocab} (one name/term per line)
"""


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    arg = argv[0] if argv else "run"
    if arg in ("-h", "--help", "help"):
        return cmd_help()
    try:
        if arg in ("quit", "stop"):
            return cmd_quit()
        if arg == "setup":
            return cmd_setup()
        if arg == "set-hotkey":
            return cmd_set_hotkey()
        if arg == "test-mic":
            return cmd_test_mic()
        if arg == "test-inject":
            return cmd_test_inject()
        if arg == "demo":
            return cmd_demo()
        if arg == "models":
            return cmd_models()
        if arg == "run":
            return cmd_run()
        print(f"unknown command: {arg}", file=sys.stderr)
        cmd_help()
        return 2
    except KeyboardInterrupt:
        print()
        return 130


def cmd_help() -> int:
    cfg = load_config()
    hk = cfg.get("hotkey_label") or str(cfg.get("hotkey") or "pause").upper()
    print(f"Hold {hk}, speak, release. Tap a non-typing key for hands-free.")
    print(HELP.format(config=CONFIG_PATH, vocab=VOCAB_PATH))
    return 0


def cmd_quit() -> int:
    pid = already_running()
    if not pid:
        print("mintflow is not running")
        return 0
    if not _kill_pid(pid):
        print(f"could not stop pid {pid}", file=sys.stderr)
        return 1
    _clear_pid()
    print(f"stopped {pid}")
    return 0


def cmd_models() -> int:
    from mintflow.gpu import detect_gpu, recommend_model

    cfg = load_config()
    gpu = detect_gpu()
    rec = recommend_model(gpu)
    print(f"GPU: {gpu.name} ({gpu.vendor})")
    print(f"VRAM: {gpu.vram_gb:.1f} GB")
    print(f"RAM:  {gpu.ram_gb:.1f} GB")
    print(f"Recommended: model={rec.model} device={rec.device} compute_type={rec.compute_type}")
    print(
        "Configured:  "
        f"model={cfg.get('model')} device={cfg.get('device')} "
        f"compute_type={cfg.get('compute_type')}"
    )
    print(f"Cleanup: {cfg.get('cleanup')} / {cfg.get('ollama_model')}")
    return 0


def cmd_setup() -> int:
    from mintflow.gpu import detect_gpu, recommend_model, setup_auto_config

    cfg = load_config()
    print("Detecting hardware...")
    gpu = detect_gpu()
    rec = recommend_model(gpu)
    cfg = setup_auto_config(cfg)
    print(f"  {gpu.name} ({gpu.vendor}, {gpu.vram_gb:.1f} GB VRAM, {gpu.ram_gb:.1f} GB RAM)")
    print(f"  Whisper: {cfg['model']} on {cfg['device']} ({cfg['compute_type']})")
    print(f"  Recommended: {rec.model} / {rec.device} / {rec.compute_type}")
    _ensure_ollama_model(cfg)
    print("Set your hotkey (window pops up, press a key)...")
    cmd_set_hotkey(stop_daemon=False)
    cfg = load_config()
    hk = cfg.get("hotkey_label") or str(cfg.get("hotkey") or "pause").upper()
    print(f"Ready. Hold {hk} to talk.")
    return 0


def cmd_set_hotkey(stop_daemon: bool = True, timeout_s: float = 15) -> int:
    if stop_daemon:
        pid = already_running()
        if pid:
            _kill_pid(pid)
            _clear_pid()
            time.sleep(0.2)
    cfg = load_config()
    try:
        backend = _make_backend(cfg)
        captured = backend.capture_hotkey(timeout_s)
    except Exception as e:
        print(f"hotkey capture failed: {e}", file=sys.stderr)
        return 1
    if not captured or "spec" not in captured:
        print("no key captured")
        return 1
    from mintflow.app import apply_captured_hotkey

    apply_captured_hotkey(cfg, captured)
    label = captured.get("label") or captured["spec"]
    print(f"hotkey set: {label} ({captured['spec']})")
    return 0


def cmd_test_mic() -> int:
    import numpy as np

    from mintflow.audio import Recorder
    from mintflow.engine import Engine

    cfg = _resolved_cfg()
    log("recording 2.5s from default source...")
    rec = Recorder(int(cfg["sample_rate"]))
    rec.start()
    time.sleep(2.5)
    audio = rec.stop()
    rms = float(np.sqrt(np.mean(np.square(audio))) + 1e-12) if audio.size else 0.0
    log(f"samples={audio.size} rms={rms:.4f}")
    eng = Engine(cfg)
    eng.preload()
    if eng.model is None:
        print(eng.error or "whisper failed to load", file=sys.stderr)
        return 1
    raw = eng.transcribe(audio)
    print("RAW:", raw)
    print("CLEAN:", eng.rewrite(raw))
    return 0


def cmd_test_inject() -> int:
    cfg = load_config()
    print("Click the target window. Injecting in 1.2s...")
    time.sleep(1.2)
    try:
        backend = _make_backend(cfg)
        backend.paste_text(
            "mintflow paste test. If you can see this, injection works.",
            int(cfg.get("restore_clipboard_ms", 400)),
        )
    except Exception as e:
        print(f"inject failed: {e}", file=sys.stderr)
        return 1
    print("injected")
    return 0


def cmd_demo() -> int:
    cfg = load_config()
    backend = _make_backend(cfg)
    seq = ["listening", "transcribing", "cleaning", "done"]
    sample = "Can you tell the team the launch is slipping to Monday?"
    i = {"n": 0}

    def step() -> bool:
        status = seq[i["n"] % len(seq)]
        backend.overlay_set_status(status, 0.5)
        backend.overlay_set_text(sample if status == "listening" else "")
        i["n"] += 1
        if i["n"] > 8:
            backend.overlay_hide()
            backend.quit()
            return False
        return True

    backend.overlay_place()
    backend.overlay_set_status("listening", 0.6)
    backend.overlay_set_text(sample)
    backend.overlay_show()
    backend.call_later(700, step)
    backend.run()
    return 0


def cmd_run() -> int:
    cfg = load_config()
    if str(cfg.get("model", "auto")).lower() == "auto":
        print("First run: configuring mintflow...")
        cmd_setup()
        cfg = load_config()
    else:
        from mintflow.gpu import setup_auto_config

        cfg = setup_auto_config(cfg)

    running = already_running()
    if running:
        from mintflow.app import notify

        notify(f"mintflow is already running (pid {running})")
        return 0

    write_pid()
    load_vocabulary()
    from mintflow.app import FlowApp

    app = FlowApp(cfg)

    def handle_sig(*_a) -> None:
        try:
            app.backend.call_on_main(app.shutdown)
        except Exception:
            app.shutdown()

    signal.signal(signal.SIGINT, handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_sig)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handle_sig)

    app.start()
    return 0


def _resolved_cfg() -> dict:
    from mintflow.gpu import setup_auto_config

    return setup_auto_config(load_config())


def _make_backend(cfg: dict):
    from mintflow.platform import get_backend

    backend = get_backend()
    backend.cfg = cfg
    return backend


def _ensure_ollama_model(cfg: dict) -> None:
    if str(cfg.get("cleanup") or "") != "ollama":
        return
    model = str(cfg.get("ollama_model") or "qwen2.5:7b")
    url = str(cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    if _ollama_has_model(url, model):
        print(f"  Ollama model {model} already present")
        return
    ollama = shutil.which("ollama")
    if not ollama:
        print(f"  Ollama not found. Install it, then: ollama pull {model}")
        return
    print(f"  Pulling Ollama model {model}...")
    try:
        subprocess.run([ollama, "pull", model], check=False)
    except OSError as e:
        print(f"  ollama pull failed: {e}")


def _ollama_has_model(url: str, model: str) -> bool:
    try:
        import httpx

        r = httpx.get(f"{url}/api/tags", timeout=3.0)
        r.raise_for_status()
        names = [str(m.get("name") or "") for m in r.json().get("models", [])]
    except Exception:
        return False
    want = model.split(":")[0]
    for name in names:
        if name == model or name.startswith(model) or name.split(":")[0] == want:
            return True
    return False


def already_running() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    if pid == os.getpid():
        return None
    if not _pid_alive(pid):
        _clear_pid()
        return None
    if not _pid_is_mintflow(pid):
        _clear_pid()
        return None
    return pid


def write_pid() -> None:
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_clear_pid)


def _clear_pid() -> None:
    try:
        if not PID_PATH.exists():
            return
        current = PID_PATH.read_text(encoding="utf-8").strip()
        if current == str(os.getpid()) or not _pid_alive(int(current)):
            PID_PATH.unlink(missing_ok=True)
    except (ValueError, OSError):
        try:
            PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return int(ctypes.GetLastError()) == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_is_mintflow(pid: int) -> bool:
    if sys.platform.startswith("linux"):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
            return "mintflow" in cmd
        except OSError:
            return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            line = (out.stdout or "").lower()
            return "python" in line or "mintflow" in line
        except Exception:
            return True
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return "mintflow" in (out.stdout or "")
    except Exception:
        return True


def _kill_pid(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=8,
                check=False,
            )
            return r.returncode == 0
        except Exception:
            return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False
