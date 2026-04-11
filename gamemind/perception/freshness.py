"""Amendment A1 Perception Freshness Contract (docs/final-design.md §1.1.A).

Bounded-size-1 latest-wins queue between capture and inference.
`frame_age_ms` is computed at PerceptionResult construction time and
propagates downstream so every consumer (Layer 2, Layer 3, verify) can
decide whether to trust a frame or force a fresh capture.

750ms is the default `freshness_budget_ms` (2x nominal 2Hz tick
interval). Adapter-overridable via the adapter's `perception.freshness_budget_ms`
field if a game's latency tolerance differs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

DEFAULT_FRESHNESS_BUDGET_MS: float = 750.0
"""Default `frame_age_ms` threshold above which a frame is considered stale.

2x the nominal 2Hz tick interval. Adapter-overridable per game.
"""


@dataclass
class PerceptionResult:
    """Result of a single perception tick.

    Carries `frame_age_ms` per Amendment A1 so downstream consumers
    (Layer 2 stuck detector, Layer 3 brain wake triggers, verify engine)
    can inspect staleness and route recovery policies.

    Fields:
      frame_id: stable UUID-ish identifier for the underlying capture.
      capture_ts_monotonic_ns: monotonic_ns when Layer 0 captured the
                               frame (NOT when perception started).
      frame_age_ms: age at construction time = `monotonic_now - capture_ts`.
                    Updated on every downstream inspection if re-measured.
      parsed: parsed JSON output from the VLM, or None on parse failure.
      raw_text: raw string response from the VLM.
      latency_ms: wall-clock ms spent on this perception call
                  (capture → VLM → parse → result).
      error: error code string (e.g. "E106") if the call failed, None
             on success.
      backend_meta: escape hatch for backend-specific metadata
                    (Ollama's total_duration_ns, eval_count, etc).
    """

    frame_id: str
    capture_ts_monotonic_ns: int
    frame_age_ms: float
    parsed: dict[str, Any] | None
    raw_text: str
    latency_ms: float
    error: str | None = None
    backend_meta: dict[str, Any] = field(default_factory=dict)

    def age_now_ms(self) -> float:
        """Recompute frame_age_ms at call time (for downstream staleness checks)."""
        now_ns = time.monotonic_ns()
        return (now_ns - self.capture_ts_monotonic_ns) / 1_000_000.0


def is_stale(
    result: PerceptionResult,
    *,
    budget_ms: float = DEFAULT_FRESHNESS_BUDGET_MS,
    recompute: bool = True,
) -> bool:
    """Return True iff the result is older than the freshness budget.

    If `recompute=True` (default), re-measures age from
    `capture_ts_monotonic_ns` at call time — reflects true current
    staleness, not the stored `frame_age_ms` at result construction.
    """
    age = result.age_now_ms() if recompute else result.frame_age_ms
    return age > budget_ms


class FreshnessQueue:
    """Bounded-size-1 latest-wins queue per Amendment A1.

    Producer (Layer 0 capture loop) calls `put()`. Consumer (Layer 1
    perception inference) calls `take()`. Frames that arrive while the
    queue is full OVERWRITE the pending frame, discarding the prior one.
    Every drop emits a callback (for `events.jsonl` `perception_stale_dropped`
    emission).

    This is NOT a general-purpose queue. It has exactly one semantic:
    "the consumer always gets the most recent producer value." Use a
    standard `queue.Queue` for anything else.

    Thread-safe via a single Lock — minimal contention because put/take
    are O(1) and hold the lock for microseconds.
    """

    def __init__(self) -> None:
        self._slot: PerceptionResult | None = None
        self._lock = threading.Lock()
        self._drop_count: int = 0

    def put(self, result: PerceptionResult) -> bool:
        """Publish a new perception result. Returns True iff this put
        overwrote a pending value (caller should emit a drop event)."""
        with self._lock:
            dropped = self._slot is not None
            if dropped:
                self._drop_count += 1
            self._slot = result
            return dropped

    def take(self) -> PerceptionResult | None:
        """Consume the current value. Returns None if the slot is empty."""
        with self._lock:
            result = self._slot
            self._slot = None
            return result

    def peek(self) -> PerceptionResult | None:
        """Return the current value without consuming."""
        with self._lock:
            return self._slot

    @property
    def drop_count(self) -> int:
        """Total number of frames dropped since queue creation."""
        with self._lock:
            return self._drop_count

    def reset_drop_count(self) -> None:
        with self._lock:
            self._drop_count = 0
