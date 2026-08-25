"""OS detection and platform backend factory."""

from __future__ import annotations

import sys


class BackendUnavailable(RuntimeError):
    """A desktop dependency is missing. The message is shown to the user as-is,
    so it must say what to install, not what failed internally."""


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
    raise BackendUnavailable(
        f"mintflow does not support {sys.platform} yet. It runs on Linux (X11), "
        "macOS, and Windows."
    )


__all__ = ["BackendUnavailable", "detect_os", "get_backend"]
