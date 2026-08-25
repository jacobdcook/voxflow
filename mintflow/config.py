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
    "max_seconds": 600,
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
    "sound_volume": 0.3,
    "restore_clipboard_ms": 450,
    "stream_interval_s": 1.0,
}

# key -> (python type, minimum, maximum). Values outside the range are clamped;
# values of the wrong type fall back to the default.
_LIMITS = {
    "handsfree_max_s": (int, 5, 3600),
    "max_seconds": (int, 5, 7200),
    "sample_rate": (int, 8000, 48000),
    "tap_ms": (int, 40, 2000),
    "repeat_ms": (int, 0, 1000),
    "min_seconds": (float, 0.0, 10.0),
    "sound_volume": (float, 0.0, 1.0),
    "restore_clipboard_ms": (int, 0, 60000),
    "stream_interval_s": (float, 0.2, 30.0),
    "sounds": (bool, None, None),
}
_TEXT_KEYS = (
    "hotkey",
    "model",
    "device",
    "compute_type",
    "language",
    "style",
    "cleanup",
    "ollama_model",
    "ollama_url",
)

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


def _as_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return None


def _is_local_url(url: str) -> bool:
    host = str(url).split("//", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    if host.startswith("["):  # bracketed IPv6
        host = host[1:].split("]", 1)[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host.lower() in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "")


def sanitize_config(cfg: dict) -> dict:
    """Repair a config loaded from disk so older or hand-edited files still run.

    Wrong-typed values fall back to the default, out-of-range numbers are
    clamped. Unknown keys are kept so nothing a user added is silently lost.
    """
    clean = dict(cfg)
    for key, (kind, low, high) in _LIMITS.items():
        raw = clean.get(key, DEFAULTS[key])
        if kind is bool:
            value = _as_bool(raw)
            clean[key] = DEFAULTS[key] if value is None else value
            continue
        try:
            # json.loads accepts Infinity and NaN, so int() can overflow here.
            value = kind(raw)
        except (TypeError, ValueError, OverflowError):
            log(f"config: {key}={raw!r} is not a number, using {DEFAULTS[key]!r}")
            value = kind(DEFAULTS[key])
        if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
            value = kind(DEFAULTS[key])
        clamped = max(low, min(high, value))
        if clamped != value:
            log(f"config: {key}={value!r} out of range, using {clamped!r}")
        clean[key] = kind(clamped)
    for key in _TEXT_KEYS:
        raw = clean.get(key, DEFAULTS[key])
        if raw is None or not str(raw).strip():
            clean[key] = DEFAULTS[key]
        else:
            clean[key] = str(raw).strip()
    if clean["min_seconds"] >= clean["handsfree_max_s"]:
        clean["min_seconds"] = DEFAULTS["min_seconds"]
    if clean["max_seconds"] < clean["handsfree_max_s"]:
        clean["max_seconds"] = clean["handsfree_max_s"]
    url = str(clean.get("ollama_url", ""))
    if clean.get("cleanup") == "ollama" and not _is_local_url(url):
        log(
            f"WARNING: ollama_url points at {url}, which is not this computer. "
            "Every transcript will be sent there. Set it back to "
            f"{DEFAULTS['ollama_url']} to keep mintflow fully local."
        )
    if "hotkey_typing" in clean:
        typing = _as_bool(clean["hotkey_typing"])
        if typing is None:
            clean.pop("hotkey_typing")
        else:
            clean["hotkey_typing"] = typing
    return clean


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        raw = None
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            log(f"config read failed, using defaults: {e}")
        if isinstance(raw, dict):
            cfg.update(raw)
        elif raw is not None:
            log(f"config is a {type(raw).__name__}, not an object. Using defaults.")
    else:
        save_config(cfg)
    return sanitize_config(cfg)


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
