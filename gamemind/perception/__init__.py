"""Layer 1 — Perception.

Continuous 2-3 Hz local VLM inference via Ollama. The perception daemon
runs on a dedicated thread, captures frames via Layer 0, calls
`OllamaBackend.chat()` with the per-frame reflex prompt, and publishes
`PerceptionResult`s to the downstream Layer 2 trigger detector.

Amendment A1 enforces a latest-wins bounded-size-1 queue between capture
and inference — see `gamemind.perception.freshness` for the contract
implementation.
"""

from __future__ import annotations

from gamemind.perception.freshness import (
    DEFAULT_FRESHNESS_BUDGET_MS,
    FreshnessQueue,
    PerceptionResult,
    is_stale,
)
from gamemind.perception.ollama_backend import OllamaBackend

__all__ = [
    "DEFAULT_FRESHNESS_BUDGET_MS",
    "FreshnessQueue",
    "OllamaBackend",
    "PerceptionResult",
    "is_stale",
]
