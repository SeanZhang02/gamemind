"""ctypes helpers for HWND / window title / window rect lookup.

Phase C Step 1 Batch A — real bindings. These helpers let the WGC and
DXGI backends translate the Protocol's `hwnd: int` parameter into the
concrete inputs each backend needs (window title string for
`windows-capture`, rectangle for `dxcam` region grab).

All functions are Windows-only. On non-Windows they raise NotImplementedError
at import time so the CI ubuntu-latest runner doesn't choke on the ctypes
imports when it doesn't need them.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

if sys.platform != "win32":
    # On CI (ubuntu-latest) these functions are never called — the capture
    # tests that need them are marked integration-only and skip on non-Win.
    # But we still want the module to import cleanly so dependent modules
    # can be imported/type-checked.
    _user32 = None  # type: ignore[assignment]
else:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetClientRect.restype = wintypes.BOOL
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.ClientToScreen.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND


@dataclass(frozen=True)
class WindowRect:
    """Screen-coordinate rectangle covering a window's client area."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_region(self) -> tuple[int, int, int, int]:
        """Return as (left, top, right, bottom) tuple for dxcam.grab(region=...)."""
        return (self.left, self.top, self.right, self.bottom)


def _ensure_win32() -> None:
    if _user32 is None:
        raise NotImplementedError("win32 helpers are only available on Windows")


def is_valid_hwnd(hwnd: int) -> bool:
    """True iff the HWND currently identifies a live top-level window."""
    _ensure_win32()
    if hwnd == 0:
        return False
    return bool(_user32.IsWindow(wintypes.HWND(hwnd)))


def get_window_title(hwnd: int) -> str:
    """Return a window's title (best-effort).

    Returns an empty string if the window has no title or the HWND is
    invalid. Never raises for caller convenience — the capture backend
    checks is_valid_hwnd first.
    """
    _ensure_win32()
    if not is_valid_hwnd(hwnd):
        return ""
    length = _user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
    return buffer.value


def get_window_rect(hwnd: int) -> WindowRect:
    """Return the window's client-area rectangle in screen coordinates.

    Uses GetClientRect + ClientToScreen to get the inner client area
    (excluding title bar / borders). This is the correct input for
    dxcam's region grab — passing the full window frame would include
    Windows chrome.
    """
    _ensure_win32()
    if not is_valid_hwnd(hwnd):
        raise ValueError(f"invalid HWND: {hwnd}")
    client = wintypes.RECT()
    ok = _user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client))
    if not ok:
        raise OSError(f"GetClientRect failed for HWND {hwnd}")
    # Convert top-left from client to screen
    tl = wintypes.POINT(0, 0)
    _user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(tl))
    return WindowRect(
        left=tl.x,
        top=tl.y,
        right=tl.x + client.right,
        bottom=tl.y + client.bottom,
    )


def find_window_by_title(title: str) -> int:
    """Return the HWND of a top-level window with an exact title match.

    Returns 0 if no match. Wraps `user32.FindWindowW` — exact match only,
    not a substring.
    """
    _ensure_win32()
    return int(_user32.FindWindowW(None, title))


__all__ = [
    "WindowRect",
    "find_window_by_title",
    "get_window_rect",
    "get_window_title",
    "is_valid_hwnd",
]
