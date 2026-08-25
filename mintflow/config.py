"""Platform-aware paths, JSON config, vocabulary, and logging."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

APP_NAME = "mintflow"


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def _pid_suffix() -> str:
    if hasattr(os, "getuid"):
        return str(os.getuid())
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "user"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "mintflow.log"
VOCAB_PATH = CONFIG_DIR / "vocabulary.txt"
PID_PATH = Path(tempfile.gettempdir()) / f"mintflow-{_pid_suffix()}.pid"

DEFAULTS = {
    "hotkey": "pause",
    "handsfree_max_s": 180,
    "model": "auto",
    "device": "auto",
    "compute_type": "auto",
    "language": "en",
    "style": "casual",
    "cleanup": "ollama",
    "ollama_model": "qwen2.5:7b",
    "ollama_url": "http://127.0.0.1:11434",
    "sample_rate": 16000,
    "tap_ms": 220,
    "repeat_ms": 80,
    "min_seconds": 0.35,
    "sounds": True,
    "restore_clipboard_ms": 450,
    "stream_interval_s": 1.0,
}

STYLE_HINTS = {
    "formal": "Complete sentences, professional, ready to send as email.",
    "casual": "Natural written English. Complete sentences. Not stiff.",
    "very-casual": "Lowercase is fine. Short. Sounds like a chat message.",
}

JUNK_TRANSCRIPTS = {
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "thank you so much for watching",
    "you",
    "bye",
    "so",
    "subtitles by the amara org community",
}


def log(msg: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(f"[mintflow] {msg}", flush=True)


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            log(f"config read failed, using defaults: {e}")
    else:
        save_config(cfg)
    return cfg


def load_vocabulary() -> str:
    try:
        words = [
            w.strip()
            for w in VOCAB_PATH.read_text(encoding="utf-8").splitlines()
            if w.strip() and not w.strip().startswith("#")
        ]
    except OSError:
        return ""
    return ", ".join(words[:80])
