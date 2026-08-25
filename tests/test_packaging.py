"""Packaging check: metadata is valid and only intended files ship."""
import os
import pathlib
import sys
import tempfile

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
os.chdir(ROOT_DIR)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  <- {detail}"))
    if not cond:
        fails.append(name)


try:
    import tomllib
except ImportError:  # 3.10
    tomllib = None

if tomllib:
    meta = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    proj = meta["project"]
    check("project metadata parses", bool(proj.get("name") and proj.get("version")))
    check(
        "console script points at cli:main",
        proj["scripts"]["voxflow"] == "voxflow.cli:main",
        proj["scripts"],
    )
    import voxflow

    check(
        "pyproject version matches package __version__",
        proj["version"] == voxflow.__version__,
        f"{proj['version']} vs {voxflow.__version__}",
    )
    deps = " ".join(proj["dependencies"])
    for need in ("numpy", "sounddevice", "faster-whisper", "httpx"):
        check(f"declares {need}", need in deps)
    extras = proj["optional-dependencies"]
    check("linux extra has python-xlib", "python-xlib" in " ".join(extras["linux"]))
    check("desktop extra has pynput", "pynput" in " ".join(extras["desktop"]))

from setuptools import setup

sys.argv = ["setup.py", "--quiet", "egg_info", "--egg-base", tempfile.mkdtemp()]
dist = setup(
    name="voxflow",
    packages=__import__("setuptools").find_packages(where=".", include=["voxflow*"]),
    script_args=sys.argv[1:],
)
pkgs = set(dist.packages or [])
check(
    "packages are exactly the voxflow tree",
    pkgs == {"voxflow", "voxflow.platform"},
    sorted(pkgs),
)
check(
    "the v0 prototype is not importable as a top level module named voxflow",
    __import__("voxflow").__file__.endswith("voxflow/__init__.py"),
    __import__("voxflow").__file__,
)

# every module must import without a display or optional desktop deps
import importlib

for mod in ("voxflow.config", "voxflow.gpu", "voxflow.cleanup", "voxflow.audio",
            "voxflow.engine", "voxflow.app", "voxflow.cli", "voxflow.platform"):
    try:
        importlib.import_module(mod)
        ok, why = True, ""
    except Exception as e:
        ok, why = False, repr(e)
    check(f"import {mod}", ok, why)

print()
print("FAILURES:", fails if fails else "none")
sys.exit(1 if fails else 0)
