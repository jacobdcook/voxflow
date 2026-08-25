import json
import os
import shutil
import subprocess
import sys
import tempfile

CASES = {
    "list_json": "[1, 2, 3]",
    "null_json": "null",
    "string_json": '"hello"',
    "truncated": '{"hotkey": "pause",',
    "infinity": '{"tap_ms": Infinity, "stream_interval_s": NaN, "sample_rate": -Infinity}',
    "nested_junk": '{"tap_ms": {"a": 1}, "sounds": [], "min_seconds": [1, 2]}',
    "bad_types": json.dumps(
        {"sample_rate": "sixteen thousand", "tap_ms": None, "min_seconds": "x",
         "sounds": "yes", "handsfree_max_s": -5, "restore_clipboard_ms": "abc",
         "stream_interval_s": 0, "hotkey": None, "model": None}
    ),
    "v0_config": json.dumps(
        {"hotkey": "keycode:49", "hotkey_typing": True, "handsfree_max_s": 180,
         "model": "large-v3", "device": "cuda", "compute_type": "float16",
         "language": "en", "style": "casual", "cleanup": "ollama",
         "ollama_model": "qwen2.5:14b", "ollama_url": "http://127.0.0.1:11434",
         "sample_rate": 16000, "tap_ms": 220, "repeat_ms": 80,
         "min_seconds": 0.35, "sounds": True, "restore_clipboard_ms": 450,
         "hotkey_label": "GRAVE"}
    ),
}

SNIPPET = (
    "from mintflow.config import load_config\n"
    "c = load_config()\n"
    "import mintflow.app as A\n"
    "print('sample_rate', repr(c['sample_rate']), int(c['sample_rate']))\n"
    "print('tap_ms', repr(c['tap_ms']), int(c['tap_ms']))\n"
    "print('min_seconds', float(c['min_seconds']))\n"
    "print('stream_interval_s', float(c['stream_interval_s']))\n"
    "print('handsfree_max_s', int(c['handsfree_max_s']))\n"
    "print('restore', int(c['restore_clipboard_ms']))\n"
    "print('typing', A.is_typing_key(str(c.get('hotkey') or 'pause')))\n"
)

fails = []

for name, body in CASES.items():
    root = tempfile.mkdtemp(prefix="mfcfg-")
    d = os.path.join(root, "mintflow")
    os.makedirs(d)
    with open(os.path.join(d, "config.json"), "w") as f:
        f.write(body)
    env = dict(os.environ, XDG_CONFIG_HOME=root)
    r = subprocess.run(
        [sys.executable, "-c", SNIPPET], env=env, capture_output=True, text=True
    )
    ok = r.returncode == 0
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)
        for line in (r.stdout + r.stderr).strip().splitlines()[-6:]:
            print("    " + line)
    shutil.rmtree(root, ignore_errors=True)

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
