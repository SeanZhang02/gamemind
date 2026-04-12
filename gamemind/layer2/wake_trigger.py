"""§1.4 W1-W5 wake-trigger evaluator.

Pure function of (perception state, flags, detector results) → WakeEvaluation.
No I/O, no budget awareness, no side effects. The runner owns budget checks
and event emission.

W1: session start (always fires exactly once)
W2: stuck detector (motion-quiet + no predicates + no actions, §1.4)
    OR action repetition guard bypass (Amendment A13 §1.8)
W3: abort condition edge (health < threshold, time limit, etc.)
W4: vision-critic escalation — NOT IN SCOPE for v1 (returns None)
W5: success-check candidate (all success predicates fired)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from gamemind.layer2.action_guard import ActionRepetitionGuard
from gamemind.layer2.stuck_detector import StuckDetector
from gamemind.perception.freshness import PerceptionResult

WakeReason = Literal["w1_task_start", "w2_stuck", "w3_abort", "w4_critic", "w5_verify"]


@dataclass
class WakeEvaluation:
    reason: WakeReason | None
    payload: dict[str, Any] = field(default_factory=dict)


_NO_WAKE = WakeEvaluation(reason=None)


class WakeTriggerEvaluator:
    def __init__(
        self,
        *,
        stuck: StuckDetector,
        guard: ActionRepetitionGuard,
    ) -> None:
        self._stuck = stuck
        self._guard = guard

    def on_session_start(self, task: str, adapter_name: str) -> WakeEvaluation:
        return WakeEvaluation(
            reason="w1_task_start",
            payload={"task": task, "adapter": adapter_name},
        )

    def on_perception_tick(
        self,
        result: PerceptionResult,
        *,
        frame_bytes: bytes,
        predicate_fired: bool,
        action_executed: bool,
        last_action_hash: str | None,
        abort_triggered: bool,
        ts_ns: int,
    ) -> WakeEvaluation:
        if abort_triggered:
            return WakeEvaluation(
                reason="w3_abort",
                payload={"frame_id": result.frame_id, "trigger": "abort_condition"},
            )

        if last_action_hash is not None:
            guard_fire = self._guard.record_action(last_action_hash, ts_ns)
            if guard_fire:
                return WakeEvaluation(
                    reason="w2_stuck",
                    payload={
                        "frame_id": result.frame_id,
                        "trigger": "action_repetition_guard",
                        "repeated_hash": last_action_hash,
                    },
                )

        stuck_result = self._stuck.update(
            frame_bytes=frame_bytes,
            predicate_fired=predicate_fired,
            action_executed=action_executed,
            ts_ns=ts_ns,
        )
        if stuck_result.is_stuck:
            return WakeEvaluation(
                reason="w2_stuck",
                payload={
                    "frame_id": result.frame_id,
                    "trigger": "stuck_detector",
                    "metric": stuck_result.metric_value,
                },
            )

        return _NO_WAKE

    def on_success_candidate(self, result: PerceptionResult) -> WakeEvaluation:
        return WakeEvaluation(
            reason="w5_verify",
            payload={"frame_id": result.frame_id},
        )

    def reset(self) -> None:
        self._stuck.reset()
        self._guard.reset()
