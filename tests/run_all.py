#!/usr/bin/env python3
"""Run every voxflow check. No display, no microphone, no network needed.

    python3 tests/run_all.py
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

SUITES = [
    ("state machine", "test_state_machine.py"),
    ("cleanup", "test_cleanup.py"),
    ("config migration", "test_config_migration.py"),
    ("security", "test_security.py"),
    ("packaging", "test_packaging.py"),
    ("readme", "test_readme.py"),
]


def main() -> int:
    failed = []
    for title, script in SUITES:
        print(f"\n=== {title} " + "=" * (60 - len(title)))
        result = subprocess.run(
            [sys.executable, str(HERE / script)], cwd=ROOT, text=True
        )
        if result.returncode != 0:
            failed.append(title)

    print("\n" + "=" * 68)
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print(f"All {len(SUITES)} suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
