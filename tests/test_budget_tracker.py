"""Tests for gamemind/brain/budget_tracker.py."""

from __future__ import annotations

import pytest

from gamemind.brain.budget_tracker import BudgetExceededError, BudgetTracker


def test_starts_at_zero() -> None:
    t = BudgetTracker(limit_usd=1.0)
    assert t.total_usd == 0.0
    assert t.call_count == 0
    assert not t.exceeded()


def test_records_cost() -> None:
    t = BudgetTracker(limit_usd=1.0)
    t.record(0.05)
    assert t.total_usd == 0.05
    assert t.call_count == 1


def test_raises_on_exceed() -> None:
    t = BudgetTracker(limit_usd=0.10)
    t.record(0.05)
    t.record(0.04)
    with pytest.raises(BudgetExceededError) as exc_info:
        t.record(0.02)
    assert exc_info.value.total_usd > 0.10
    assert exc_info.value.limit_usd == 0.10
    assert t.exceeded()


def test_multiple_records_accumulate() -> None:
    t = BudgetTracker(limit_usd=10.0)
    for _ in range(100):
        t.record(0.01)
    assert t.call_count == 100
    assert abs(t.total_usd - 1.0) < 0.001
