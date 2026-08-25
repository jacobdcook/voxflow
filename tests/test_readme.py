"""README accuracy: settings table vs DEFAULTS, list numbering, dead links."""
import pathlib
import re
import sys

from mintflow.config import DEFAULTS

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


# --- settings table matches the real defaults
rows = dict(
    (m.group(1), m.group(2))
    for m in re.finditer(r"^\| `([a-z_]+)` \| `([^`]+)` \|", README, re.M)
)
documented = set(rows)
real = set(DEFAULTS) - {"hotkey_typing"}
check("every setting is documented", real <= documented, sorted(real - documented))
check("no setting documented that does not exist", documented <= real, sorted(documented - real))

wrong = []
for key, shown in rows.items():
    want = DEFAULTS.get(key)
    if isinstance(want, bool):
        want = str(want).lower()
    if str(want) != shown:
        wrong.append(f"{key}: README={shown} code={want}")
check("documented default values are correct", not wrong, wrong)

# --- ordered lists restart at 1 and step by 1
for section in re.split(r"^### ", README, flags=re.M)[1:]:
    title = section.splitlines()[0].strip()
    nums = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", section, re.M)]
    if not nums:
        continue
    check(f"{title}: steps numbered 1..{len(nums)}", nums == list(range(1, len(nums) + 1)), nums)

# --- files the README points at must exist
missing = []
for target in re.findall(r"\]\(([^)h][^)]*)\)", README):
    target = target.split("#")[0]
    if not target or target.startswith("mailto:"):
        continue
    if not (ROOT_DIR / target).exists():
        missing.append(target)
check("all local links and images resolve", not missing, missing)

# --- commands the README tells people to run must be real commands
src = (ROOT_DIR / "mintflow" / "cli.py").read_text(encoding="utf-8")
known = set(re.findall(r'arg (?:==|in \()\s*"([a-z-]+)"', src))
known |= set(re.findall(r'"([a-z-]+)"[,)]', src.split("def main")[1].split("def cmd_help")[0]))
blocks = "\n".join(re.findall(r"```(?:bash|powershell|text)?\n(.*?)```", README, re.S))
used = set(re.findall(r"^\s*mintflow ([a-z-]+)", blocks, re.M))
unknown = {c for c in used if c not in known and c != "setup"}
check("README only documents real subcommands", not unknown, sorted(unknown))

# --- anchors used in the body exist as headings
heads = {
    re.sub(r"[^a-z0-9 -]", "", h.strip().lower()).replace(" ", "-")
    for h in re.findall(r"^#{2,3} (.+)$", README, re.M)
}
bad_anchor = [a for a in re.findall(r"\]\(#([a-z0-9-]+)\)", README) if a not in heads]
check("in-page anchors resolve", not bad_anchor, bad_anchor)

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
