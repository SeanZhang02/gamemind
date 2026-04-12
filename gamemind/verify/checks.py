"""Predicate evaluation engine — v1 scope.

Supports inventory_count, time_limit, health_threshold.
vision_critic, template_match, stuck_detector deferred.

Pure function: check_predicate(pred, perception, elapsed_s) -> bool.
No I/O, no state, no exceptions on evaluation (returns False on error).
"""

from __future__ import annotations

import operator as op
from typing import Any

from gamemind.adapter.schema import AbortCondition, Predicate, SuccessCheck
from gamemind.perception.freshness import PerceptionResult

_OPS = {
    ">=": op.ge,
    ">": op.gt,
    "<=": op.le,
    "<": op.lt,
    "==": op.eq,
}


def _get_nested(d: dict[str, Any] | None, *keys: str) -> Any:
    if d is None:
        return None
    current: Any = d
    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)
    return current


def check_predicate(
    pred: Predicate,
    perception: PerceptionResult | None,
    elapsed_s: float,
) -> bool:
    if pred.type == "inventory_count":
        return _check_inventory_count(pred, perception)
    if pred.type == "time_limit":
        return _check_time_limit(pred, elapsed_s)
    if pred.type == "health_threshold":
        return _check_health_threshold(pred, perception)
    return False


def check_success(
    check: SuccessCheck,
    perception: PerceptionResult | None,
    elapsed_s: float,
) -> bool:
    if check.predicate is not None:
        return check_predicate(check.predicate, perception, elapsed_s)
    if check.all_of is not None:
        return all(check_success(sub, perception, elapsed_s) for sub in check.all_of)
    if check.any_of is not None:
        return any(check_success(sub, perception, elapsed_s) for sub in check.any_of)
    return False


def check_abort(
    condition: AbortCondition,
    perception: PerceptionResult | None,
    elapsed_s: float,
) -> bool:
    if condition.type == "time_limit":
        if condition.seconds is not None:
            return elapsed_s >= condition.seconds
        return False
    if condition.type == "health_threshold":
        if perception is None or perception.parsed is None:
            return False
        health = _get_nested(perception.parsed, "health")
        if health is None or condition.operator is None or condition.value is None:
            return False
        fn = _OPS.get(condition.operator)
        if fn is None:
            return False
        try:
            return bool(fn(float(health), float(condition.value)))
        except (ValueError, TypeError):
            return False
    return False


def _check_inventory_count(pred: Predicate, perception: PerceptionResult | None) -> bool:
    if perception is None or perception.parsed is None:
        return False
    if pred.target is None or pred.operator is None or pred.value is None:
        return False
    count = _get_nested(perception.parsed, "inventory", pred.target)
    if count is None:
        count = _get_nested(perception.parsed, "inventory_count", pred.target)
    if count is None:
        return False
    fn = _OPS.get(pred.operator)
    if fn is None:
        return False
    try:
        return bool(fn(int(count), int(pred.value)))
    except (ValueError, TypeError):
        return False


def _check_time_limit(pred: Predicate, elapsed_s: float) -> bool:
    if pred.seconds is not None:
        return elapsed_s >= pred.seconds
    return False


def _check_health_threshold(pred: Predicate, perception: PerceptionResult | None) -> bool:
    if perception is None or perception.parsed is None:
        return False
    health = _get_nested(perception.parsed, "health")
    if health is None or pred.operator is None or pred.value is None:
        return False
    fn = _OPS.get(pred.operator)
    if fn is None:
        return False
    try:
        return bool(fn(float(health), float(pred.value)))
    except (ValueError, TypeError):
        return False
