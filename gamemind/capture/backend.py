"""CaptureBackend Protocol and result types.

Per autoplan Amendment A12 (docs/final-design.md §10.6.C), the Protocol
signature is frozen at Phase C Step 1 so WGC, DXGI, and any future
fallback backend satisfy the same interface.

Per autoplan Amendment A1 (Perception Freshness Contract), every
CaptureResult carries `frame_age_ms` = `monotonic_now - capture_ts` so
downstream perception / brain / action consumers can reject stale frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


CaptureBackendName = Literal["WGC", "DXGI"]


@dataclass
class CaptureResult:
    """Result of a single capture call.

    Fields (all post-Amendment-A1):
      frame_bytes: WEBP-encoded frame bytes (quality 95, lossless-ish).
      frame_age_ms: age at result-construction time, for staleness checks.
      capture_backend: which backend produced this frame (for metrics).
      variance: per-frame variance metric used by the black-frame selector.
      width, height: pixel dimensions after any resolution negotiation.
    """

    frame_bytes: bytes
    frame_age_ms: float
    capture_backend: CaptureBackendName
    variance: float
    width: int
    height: int


class CaptureBackend(Protocol):
    """Capture backend interface.

    Implementations:
      - `gamemind.capture.wgc_backend.WGCBackend` (primary, Step 1 scope)
      - `gamemind.capture.dxgi_backend.DXGIBackend` (fallback, Step 1 scope)

    `capture(hwnd, timeout_ms)` must:
      - Return a CaptureResult with `frame_age_ms < timeout_ms`
      - Raise a gamemind.errors.* exception on failure (see errors.py)
      - Never block indefinitely; honor the timeout

    `liveness()` returns True if the backend is healthy and ready to capture.
    """

    def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult: ...

    def liveness(self) -> bool: ...
