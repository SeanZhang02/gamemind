"""DXGI Desktop Duplication backend stub.

Phase C Step 1 scaffolds this module as a placeholder. Real `dxcam`
integration lands in a follow-up commit as the exclusive-fullscreen
fallback when WGC returns black frames (per §6 Step 1 selector heuristic).
"""

from __future__ import annotations

from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.errors import DXGIInitError


class DXGIBackend:
    """Stub implementation — raises DXGIInitError until the real binding lands.

    Integration target: https://pypi.org/project/dxcam/ — GPU-side frame
    grabbing via Desktop Duplication API, exclusive-fullscreen-safe.
    """

    def __init__(self) -> None:
        self._initialized = False

    def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult:
        if not self._initialized:
            raise DXGIInitError(
                cause="DXGIBackend stub: real dxcam binding not yet implemented",
                hwnd=hwnd,
            )
        # Unreachable until initialized; for type-checker.
        return CaptureResult(
            frame_bytes=b"",
            frame_age_ms=0.0,
            capture_backend="DXGI",
            variance=0.0,
            width=0,
            height=0,
        )

    def liveness(self) -> bool:
        return self._initialized


__all__ = ["DXGIBackend", "CaptureBackend", "CaptureResult"]
