"""Layer 0 — Capture.

WGC (Windows Graphics Capture) primary, DXGI (Desktop Duplication) fallback.
Backend selection is via a black-frame heuristic; see `selector.py`.

Per autoplan Amendment A12, `CaptureBackend` is a Protocol so both WGC and
DXGI implementations satisfy the same interface.
"""

from __future__ import annotations

from gamemind.capture.backend import CaptureBackend, CaptureResult

__all__ = ["CaptureBackend", "CaptureResult"]
