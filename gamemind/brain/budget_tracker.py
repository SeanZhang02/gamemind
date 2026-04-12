"""Session-level API cost budget tracker.

Wraps brain.chat() calls with cumulative cost tracking. Raises
BudgetExceededError when the session total exceeds the configured
limit. Checked AFTER each call (we pay regardless once issued).
"""

from __future__ import annotations

import threading


class BudgetExceededError(RuntimeError):
    """Raised when session brain API spend exceeds the configured limit."""

    def __init__(self, total_usd: float, limit_usd: float) -> None:
        self.total_usd = total_usd
        self.limit_usd = limit_usd
        super().__init__(
            f"Session brain API budget exceeded: ${total_usd:.4f} > ${limit_usd:.4f} limit"
        )


class BudgetTracker:
    def __init__(self, limit_usd: float) -> None:
        self._limit = limit_usd
        self._total: float = 0.0
        self._lock = threading.Lock()
        self._call_count = 0

    def record(self, cost_usd: float) -> None:
        with self._lock:
            self._total += cost_usd
            self._call_count += 1
            if self._total > self._limit:
                raise BudgetExceededError(self._total, self._limit)

    @property
    def total_usd(self) -> float:
        with self._lock:
            return self._total

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_count

    def exceeded(self) -> bool:
        with self._lock:
            return self._total > self._limit
