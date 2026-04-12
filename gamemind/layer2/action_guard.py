"""§1.8 Amendment A13 — Action Repetition Guard.

Ring buffer of last N (action_hash, ts_ns) tuples. If the same action_hash
appears >max_repeats times in a window_s window AND no predicate_fired in
the same window, force an immediate W2 stuck trigger.

Interface (post-reviewer fix):
  mark_predicate_fired(ts_ns)  — accumulates predicate state in the window
  record_action(hash, ts_ns)   — records action + evaluates guard
"""

from __future__ import annotations

import time
from collections import deque


class ActionRepetitionGuard:
    def __init__(
        self,
        *,
        ring_size: int = 20,
        window_s: float = 10.0,
        max_repeats: int = 5,
    ) -> None:
        self._ring: deque[tuple[str, int]] = deque(maxlen=ring_size)
        self._window_ns = int(window_s * 1_000_000_000)
        self._max_repeats = max_repeats
        self._predicate_timestamps: deque[int] = deque()

    def mark_predicate_fired(self, ts_ns: int | None = None) -> None:
        if ts_ns is None:
            ts_ns = time.monotonic_ns()
        self._predicate_timestamps.append(ts_ns)
        self._prune_predicates(ts_ns)

    def record_action(self, action_hash: str, ts_ns: int | None = None) -> bool:
        """Returns True iff the guard should fire (force W2 stuck trigger)."""
        if ts_ns is None:
            ts_ns = time.monotonic_ns()
        self._ring.append((action_hash, ts_ns))
        self._prune_predicates(ts_ns)

        cutoff = ts_ns - self._window_ns
        count = sum(1 for h, t in self._ring if h == action_hash and t >= cutoff)
        if count <= self._max_repeats:
            return False

        any_predicate = any(t >= cutoff for t in self._predicate_timestamps)
        return not any_predicate

    def reset(self) -> None:
        self._ring.clear()
        self._predicate_timestamps.clear()

    def _prune_predicates(self, now_ns: int) -> None:
        cutoff = now_ns - self._window_ns
        while self._predicate_timestamps and self._predicate_timestamps[0] < cutoff:
            self._predicate_timestamps.popleft()
