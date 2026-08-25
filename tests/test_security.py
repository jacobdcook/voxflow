"""Security checks: no shell interpolation, clipboard restore cannot clobber."""
import ast
import pathlib
import sys
import threading
import time

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


# --- 1. no subprocess call may use shell=True, and none may take an f-string /
#        concatenation as the whole command (that is the shell-injection shape).
ROOT = pathlib.Path(__file__).resolve().parent.parent / "mintflow"
shell_true = []
str_cmd = []
for path in sorted(ROOT.rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # only subprocess.X(...) calls; self.tk.call and friends are not shells
        if not isinstance(fn, ast.Attribute):
            continue
        if getattr(fn.value, "id", "") != "subprocess":
            continue
        if fn.attr not in ("run", "Popen", "call", "check_output", "check_call"):
            continue
        for kw in node.keywords:
            if kw.arg == "shell" and not (
                isinstance(kw.value, ast.Constant) and kw.value.value is False
            ):
                shell_true.append(f"{path}:{node.lineno}")
        if node.args and not isinstance(node.args[0], (ast.List, ast.Tuple, ast.Name)):
            str_cmd.append(f"{path}:{node.lineno}")

check("no subprocess shell=True", not shell_true, shell_true)
check("every subprocess command is an argv list", not str_cmd, str_cmd)

# --- 2. notify() must not let transcript text break out of the osascript string
import mintflow.app as A

calls = []


class FakePopen:
    def __init__(self, argv, **kw):
        calls.append(argv)


A.subprocess.Popen = FakePopen
A.sys.platform = "darwin"
EVIL = 'hi" & (do shell script "touch /tmp/pwned") & "'
A.notify(EVIL)
script = calls[-1][-1]
check(
    "osascript body stays inside one quoted literal",
    script.count('"') % 2 == 0 and '\\"' in script,
    script,
)
check("osascript body has no raw newline", "\n" not in script, repr(script))
A.sys.platform = "linux"
calls.clear()
A.notify(EVIL)
check("notify-send passes text as its own argv entry", calls[-1][-1] == EVIL, calls[-1])

# --- 3. clipboard restore must not clobber a newer copy
import mintflow.platform.linux as LX

STATE = {"clip": b"ORIGINAL"}
LX.clipboard_get = lambda: STATE["clip"]
LX.clipboard_set_bytes = lambda data: STATE.__setitem__("clip", data)
LX.focused_is_terminal = lambda: False
LX.subprocess.run = lambda *a, **k: None

LX.inject_text("first", 200)
time.sleep(0.05)
STATE["clip"] = b"USER COPIED THIS"  # user copies while restore is pending
time.sleep(0.4)
check(
    "manual copy during the restore window survives",
    STATE["clip"] == b"USER COPIED THIS",
    STATE["clip"],
)

STATE["clip"] = b"ORIGINAL"
LX.inject_text("second", 200)
time.sleep(0.4)
check("clipboard restored when untouched", STATE["clip"] == b"ORIGINAL", STATE["clip"])

STATE["clip"] = b""
LX.inject_text("third", 150)
time.sleep(0.35)
check(
    "empty previous clipboard is not restored over our text",
    STATE["clip"] == b"third",
    STATE["clip"],
)

# back-to-back dictations: the first restore must not overwrite the second paste
STATE["clip"] = b"ORIGINAL"
LX.inject_text("aaa", 400)
time.sleep(0.05)
LX.inject_text("bbb", 400)
time.sleep(0.25)
check(
    "first restore does not clobber the second paste",
    STATE["clip"] == b"bbb",
    STATE["clip"],
)

# --- 4. config warns when transcripts would leave the machine
from mintflow.config import _is_local_url

check("localhost is local", _is_local_url("http://127.0.0.1:11434"))
check("::1 is local", _is_local_url("http://[::1]:11434"))
check("remote host is not local", not _is_local_url("http://evil.example.com:11434"))
check("lan host is not local", not _is_local_url("http://192.168.1.50:11434"))

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
