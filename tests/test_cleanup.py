"""Transcript cleanup: terminal rules, quote unwrapping, junk and sanity gates."""
import sys

from mintflow.cleanup import (
    _sane_rewrite,
    apply_terminal_rules,
    local_cleanup,
    unwrap_quotes,
)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


def eq(name, got, want):
    check(name, got == want, f"got {got!r}, want {want!r}")


# --- terminal mode strips the period Whisper always appends, but only when the
#     result is a single command rather than prose
eq("command loses its period", apply_terminal_rules("ls -la."), "ls -la")
eq(
    "command with a path loses its period",
    apply_terminal_rules("grep -r error /var/log."),
    "grep -r error /var/log",
)
eq(
    "two sentences keep their punctuation",
    apply_terminal_rules("First thing. Second thing."),
    "First thing. Second thing.",
)
eq("a question keeps its mark", apply_terminal_rules("Is it done? Yes."), "Is it done? Yes.")
eq("nothing to strip is a no-op", apply_terminal_rules("cat file"), "cat file")

# the regex fallback must honour terminal mode too, not merely skip adding a dot
for spoken in ("Git commit dash m fix the parser.", "Sudo apt update."):
    out = local_cleanup(spoken, terminal=True)
    check(f"regex fallback strips the period: {spoken!r}", not out.endswith("."), out)

eq(
    "terminal mode does not force a capital",
    local_cleanup("ls -la", terminal=True),
    "ls -la",
)

# --- quote unwrapping must not eat a real closing quote
eq("wrapper double quotes go", unwrap_quotes('"Hello world."'), "Hello world.")
eq("wrapper single quotes go", unwrap_quotes("'Hello world.'"), "Hello world.")
eq(
    "a quoted commit message survives",
    unwrap_quotes('git commit -m "fix the parser"'),
    'git commit -m "fix the parser"',
)
eq(
    "inner quotes survive",
    unwrap_quotes('She said "hi" to me.'),
    'She said "hi" to me.',
)
eq("unquoted text is untouched", unwrap_quotes("Hello world."), "Hello world.")

# --- normal cleanup
out = local_cleanup("um so I was uh thinking we could try the the new approach")
check("fillers are removed", "um" not in out.lower().split(), out)
check("output is capitalised", out[:1].isupper(), out)
check("output is punctuated", out.endswith("."), out)

eq(
    "spoken punctuation becomes a symbol",
    local_cleanup("what time is standup question mark"),
    "What time is standup?",
)
check("em dashes never survive", "—" not in local_cleanup("a — b and some more"))
eq("empty in, empty out", local_cleanup(""), "")

# --- the sanity gate that decides whether to trust the model
check("a refusal is rejected", not _sane_rewrite("As an AI, I cannot do that.", "x " * 10))
check(
    "meta commentary is rejected",
    not _sane_rewrite("Here is the cleaned text: hello", "x " * 10),
)
check(
    "a truncated answer is rejected",
    not _sane_rewrite("hello", " ".join(["word"] * 40)),
)
check(
    "a padded answer is rejected",
    not _sane_rewrite(" ".join(["word"] * 40), " ".join(["word"] * 10)),
)
check("empty is rejected", not _sane_rewrite("", "hello there friend"))
check(
    "a faithful rewrite is accepted",
    _sane_rewrite(
        "I was thinking we could try the new approach.",
        "um so I was uh thinking we could try the the new approach",
    ),
)
check("short input skips the ratio gate", _sane_rewrite("Yes.", "uh yeah"))

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
