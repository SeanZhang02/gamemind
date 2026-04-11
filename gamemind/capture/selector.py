"""Capture backend selector — black-frame heuristic.

Per §6 Step 1, the selector:
  1. Tries WGC primary. If it returns N consecutive black frames, swap.
  2. Falls over to DXGI. If DXGI also fails, raise BlackFrameThreshold.

"Black frame" is defined as `variance < VARIANCE_FLOOR` (downsampled
greyscale per-pixel variance). Default `VARIANCE_FLOOR = 0.02`.

Amendment A1 (Perception Freshness Contract) requires the selector to
propagate `frame_age_ms` unmodified from the underlying backend.
"""

from __future__ import annotations

from dataclasses import dataclass

from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.errors import BlackFrameThreshold

VARIANCE_FLOOR = 0.02
BLACK_FRAME_THRESHOLD = 5


@dataclass
class SelectorState:
    primary_black_count: int = 0
    using_fallback: bool = False


class CaptureSelector:
    """Wraps two CaptureBackend implementations with fallover logic."""

    def __init__(self, primary: CaptureBackend, fallback: CaptureBackend) -> None:
        self._primary = primary
        self._fallback = fallback
        self._state = SelectorState()

    def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult:
        backend: CaptureBackend = self._fallback if self._state.using_fallback else self._primary
        result = backend.capture(hwnd, timeout_ms=timeout_ms)
        if result.variance < VARIANCE_FLOOR and not self._state.using_fallback:
            self._state.primary_black_count += 1
            if self._state.primary_black_count >= BLACK_FRAME_THRESHOLD:
                self._state.using_fallback = True
                return self._fallback.capture(hwnd, timeout_ms=timeout_ms)
        elif self._state.using_fallback and result.variance < VARIANCE_FLOOR:
            raise BlackFrameThreshold(
                cause="Both WGC and DXGI produced black frames above threshold",
                hwnd=hwnd,
                variance=result.variance,
            )
        else:
            self._state.primary_black_count = 0
        return result


__all__ = ["CaptureSelector", "VARIANCE_FLOOR", "BLACK_FRAME_THRESHOLD"]
