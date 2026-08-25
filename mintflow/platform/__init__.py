"""OS detection and platform backend factory."""

from __future__ import annotations

import sys


def detect_os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "win32"
    return sys.platform


def get_backend():
    os_name = detect_os()
    if os_name == "linux":
        from .linux import LinuxBackend

        return LinuxBackend()
    if os_name == "darwin":
        from .macos import MacBackend

        return MacBackend()
    if os_name == "win32":
        from .windows import WindowsBackend

        return WindowsBackend()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


__all__ = ["detect_os", "get_backend"]
