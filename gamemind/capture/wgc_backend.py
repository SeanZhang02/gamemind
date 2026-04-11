"""WGC (Windows Graphics Capture) backend stub.

Phase C Step 1 scaffolds this module as a placeholder implementation of
CaptureBackend. Full `windows-capture` integration lands in a follow-up
commit once `gamemind doctor --capture` is wired up.

Per Amendment A14, any warmup / doctor prompts in this module stay generic
(no game-name literals) to satisfy Design Rule 3 in phase-c/ scope.
"""

from __future__ import annotations

from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.errors import WGCInitError, WindowNotFoundError


class WGCBackend:
    """Stub implementation — raises WGCInitError until the real binding lands.

    Integration target: https://pypi.org/project/windows-capture/ — per-HWND
    Windows Graphics Capture producing RGBA frames.
    """

    def __init__(self) -> None:
        self._initialized = False

    def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult:
        if not self._initialized:
            raise WGCInitError(
                cause="WGCBackend stub: real windows-capture binding not yet implemented",
                hwnd=hwnd,
            )
        raise WindowNotFoundError(cause="stub: no HWND matching filter", hwnd=hwnd)

    def liveness(self) -> bool:
        return self._initialized


__all__ = ["WGCBackend", "CaptureBackend", "CaptureResult"]
