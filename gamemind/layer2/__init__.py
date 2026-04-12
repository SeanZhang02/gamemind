"""Layer 2 — Trigger detection + action safety.

Stuck detector (§1.4 W2), action repetition guard (Amendment A13),
and wake-trigger evaluator (§1.4 W1-W5 dispatcher).

Pure compute, no I/O. All state is tick-level, deterministic on inputs.
"""

from __future__ import annotations

from gamemind.layer2.action_guard import ActionRepetitionGuard
from gamemind.layer2.stuck_detector import StuckCheckResult, StuckDetector
from gamemind.layer2.wake_trigger import WakeEvaluation, WakeReason, WakeTriggerEvaluator

__all__ = [
    "ActionRepetitionGuard",
    "StuckCheckResult",
    "StuckDetector",
    "WakeEvaluation",
    "WakeReason",
    "WakeTriggerEvaluator",
]
