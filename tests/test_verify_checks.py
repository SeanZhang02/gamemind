"""Tests for gamemind/verify/checks.py — v1 predicate evaluation."""

from __future__ import annotations

import time

from gamemind.adapter.schema import AbortCondition, Predicate, SuccessCheck
from gamemind.perception.freshness import PerceptionResult
from gamemind.verify.checks import check_abort, check_predicate, check_success


def _perception(parsed: dict | None = None) -> PerceptionResult:
    return PerceptionResult(
        frame_id="test",
        capture_ts_monotonic_ns=time.monotonic_ns(),
        frame_age_ms=50.0,
        parsed=parsed,
        raw_text="{}",
        latency_ms=100.0,
    )


class TestInventoryCount:
    def test_true_when_count_meets(self) -> None:
        pred = Predicate(type="inventory_count", target="log", operator=">=", value=3)
        p = _perception({"inventory": {"log": 5}})
        assert check_predicate(pred, p, 0.0) is True

    def test_false_when_count_below(self) -> None:
        pred = Predicate(type="inventory_count", target="log", operator=">=", value=3)
        p = _perception({"inventory": {"log": 2}})
        assert check_predicate(pred, p, 0.0) is False

    def test_false_when_missing_key(self) -> None:
        pred = Predicate(type="inventory_count", target="log", operator=">=", value=3)
        p = _perception({"inventory": {}})
        assert check_predicate(pred, p, 0.0) is False

    def test_false_when_no_perception(self) -> None:
        pred = Predicate(type="inventory_count", target="log", operator=">=", value=3)
        assert check_predicate(pred, None, 0.0) is False

    def test_alternative_key(self) -> None:
        pred = Predicate(type="inventory_count", target="log", operator=">=", value=3)
        p = _perception({"inventory_count": {"log": 3}})
        assert check_predicate(pred, p, 0.0) is True


class TestTimeLimit:
    def test_fires_when_exceeded(self) -> None:
        pred = Predicate(type="time_limit", seconds=600.0)
        assert check_predicate(pred, None, 601.0) is True

    def test_not_fires_before_limit(self) -> None:
        pred = Predicate(type="time_limit", seconds=600.0)
        assert check_predicate(pred, None, 599.0) is False


class TestHealthThreshold:
    def test_fires_when_below(self) -> None:
        pred = Predicate(type="health_threshold", operator="<", value=0.3)
        p = _perception({"health": 0.2})
        assert check_predicate(pred, p, 0.0) is True

    def test_not_fires_when_above(self) -> None:
        pred = Predicate(type="health_threshold", operator="<", value=0.3)
        p = _perception({"health": 0.8})
        assert check_predicate(pred, p, 0.0) is False


class TestSuccessCheck:
    def test_single_predicate(self) -> None:
        check = SuccessCheck(
            predicate=Predicate(type="inventory_count", target="log", operator=">=", value=3)
        )
        p = _perception({"inventory": {"log": 3}})
        assert check_success(check, p, 0.0) is True

    def test_all_of(self) -> None:
        check = SuccessCheck(
            all_of=[
                SuccessCheck(
                    predicate=Predicate(
                        type="inventory_count", target="log", operator=">=", value=3
                    )
                ),
                SuccessCheck(predicate=Predicate(type="time_limit", seconds=10.0)),
            ]
        )
        p = _perception({"inventory": {"log": 3}})
        assert check_success(check, p, 5.0) is False
        assert check_success(check, p, 11.0) is True


class TestAbortCondition:
    def test_time_limit_abort(self) -> None:
        cond = AbortCondition(type="time_limit", seconds=600.0)
        assert check_abort(cond, None, 601.0) is True
        assert check_abort(cond, None, 599.0) is False

    def test_health_abort(self) -> None:
        cond = AbortCondition(type="health_threshold", operator="<", value=0.3)
        p = _perception({"health": 0.1})
        assert check_abort(cond, p, 0.0) is True
