"""Unit tests for Amendment A1 Perception Freshness Contract."""

from __future__ import annotations

import time

from gamemind.perception.freshness import (
    DEFAULT_FRESHNESS_BUDGET_MS,
    FreshnessQueue,
    PerceptionResult,
    is_stale,
)


def _make_result(age_ms: float = 0.0, frame_id: str = "test-frame") -> PerceptionResult:
    """Construct a PerceptionResult with a synthetic age.

    `age_ms` controls how "old" the frame pretends to be by backdating
    `capture_ts_monotonic_ns`.
    """
    now_ns = time.monotonic_ns()
    capture_ts = now_ns - int(age_ms * 1_000_000)
    return PerceptionResult(
        frame_id=frame_id,
        capture_ts_monotonic_ns=capture_ts,
        frame_age_ms=age_ms,
        parsed={"block": "oak_log"},
        raw_text='{"block": "oak_log"}',
        latency_ms=200.0,
    )


def test_fresh_result_not_stale() -> None:
    result = _make_result(age_ms=100.0)
    assert is_stale(result) is False


def test_stale_result_is_stale() -> None:
    result = _make_result(age_ms=1200.0)
    assert is_stale(result) is True


def test_default_budget_boundary() -> None:
    # Below budget
    assert is_stale(_make_result(age_ms=700.0), recompute=False) is False
    # At budget (not strictly greater)
    assert is_stale(_make_result(age_ms=750.0), recompute=False) is False
    # Above budget
    assert is_stale(_make_result(age_ms=750.1), recompute=False) is True


def test_custom_budget() -> None:
    result = _make_result(age_ms=400.0)
    assert is_stale(result, budget_ms=500.0) is False
    assert is_stale(result, budget_ms=300.0) is True


def test_age_now_recomputes() -> None:
    result = _make_result(age_ms=100.0)
    stored_age = result.frame_age_ms
    time.sleep(0.01)  # 10ms real sleep
    now_age = result.age_now_ms()
    assert now_age > stored_age


def test_freshness_budget_default() -> None:
    assert DEFAULT_FRESHNESS_BUDGET_MS == 750.0


def test_queue_put_take_single() -> None:
    q = FreshnessQueue()
    result = _make_result(frame_id="f1")
    dropped = q.put(result)
    assert dropped is False
    taken = q.take()
    assert taken is not None
    assert taken.frame_id == "f1"
    assert q.take() is None  # queue is empty after take


def test_queue_put_overwrites_pending() -> None:
    q = FreshnessQueue()
    r1 = _make_result(frame_id="f1")
    r2 = _make_result(frame_id="f2")
    assert q.put(r1) is False
    # Second put overwrites r1 without a take in between
    assert q.put(r2) is True
    taken = q.take()
    assert taken is not None
    assert taken.frame_id == "f2"  # latest wins


def test_queue_drop_count() -> None:
    q = FreshnessQueue()
    assert q.drop_count == 0
    q.put(_make_result(frame_id="f1"))
    q.put(_make_result(frame_id="f2"))  # drops f1
    q.put(_make_result(frame_id="f3"))  # drops f2
    assert q.drop_count == 2
    q.take()  # take f3
    q.put(_make_result(frame_id="f4"))  # fresh put, no drop
    assert q.drop_count == 2


def test_queue_peek_does_not_consume() -> None:
    q = FreshnessQueue()
    q.put(_make_result(frame_id="f1"))
    peeked = q.peek()
    assert peeked is not None and peeked.frame_id == "f1"
    # Second peek still returns the same
    peeked2 = q.peek()
    assert peeked2 is not None and peeked2.frame_id == "f1"


def test_queue_reset_drop_count() -> None:
    q = FreshnessQueue()
    q.put(_make_result(frame_id="f1"))
    q.put(_make_result(frame_id="f2"))
    assert q.drop_count == 1
    q.reset_drop_count()
    assert q.drop_count == 0


def test_queue_thread_safety_smoke() -> None:
    """Smoke test for the lock — N producer threads hammering a single queue."""
    import threading

    q = FreshnessQueue()
    n_threads = 4
    n_puts_per_thread = 100

    def producer(thread_id: int) -> None:
        for i in range(n_puts_per_thread):
            q.put(_make_result(frame_id=f"t{thread_id}-{i}"))

    threads = [threading.Thread(target=producer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_puts = n_threads * n_puts_per_thread
    # We put `total_puts` times. After all puts, the slot has 1 value,
    # and drop_count = total_puts - 1 (each put after the first drops 1).
    assert q.drop_count == total_puts - 1
    final = q.take()
    assert final is not None  # some producer's last frame
