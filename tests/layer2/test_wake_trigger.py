"""Wake trigger evaluator tests — W1, W2 (stuck + guard bypass), W3, W5."""

from __future__ import annotations

import io
import time

from PIL import Image

from gamemind.layer2.action_guard import ActionRepetitionGuard
from gamemind.layer2.stuck_detector import StuckDetector
from gamemind.layer2.wake_trigger import WakeTriggerEvaluator
from gamemind.perception.freshness import PerceptionResult

NS = 1_000_000_000


def _make_frame(color: tuple[int, int, int] = (80, 80, 80)) -> bytes:
    img = Image.new("RGB", (64, 64), color)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _make_perception(frame_id: str = "test-frame") -> PerceptionResult:
    return PerceptionResult(
        frame_id=frame_id,
        capture_ts_monotonic_ns=time.monotonic_ns(),
        frame_age_ms=50.0,
        parsed={"block": "oak_log"},
        raw_text='{"block": "oak_log"}',
        latency_ms=400.0,
    )


class TestW1TaskStart:
    def test_always_fires(self) -> None:
        evaluator = WakeTriggerEvaluator(
            stuck=StuckDetector(),
            guard=ActionRepetitionGuard(),
        )
        result = evaluator.on_session_start(task="chop 3 logs", adapter_name="minecraft")
        assert result.reason == "w1_task_start"
        assert result.payload["task"] == "chop 3 logs"


class TestW2StuckDetector:
    def test_fires_after_stuck_seconds(self) -> None:
        evaluator = WakeTriggerEvaluator(
            stuck=StuckDetector(stuck_seconds=3.0, entropy_floor=0.02, lookback_seconds=1.0),
            guard=ActionRepetitionGuard(),
        )
        frame = _make_frame()
        perception = _make_perception()
        fired = False
        for i in range(10):
            result = evaluator.on_perception_tick(
                perception,
                frame_bytes=frame,
                predicate_fired=False,
                action_executed=False,
                last_action_hash=None,
                abort_triggered=False,
                ts_ns=i * NS,
            )
            if result.reason == "w2_stuck":
                fired = True
                assert result.payload["trigger"] == "stuck_detector"
                break
        assert fired


class TestW2GuardBypass:
    def test_fires_on_repeated_action(self) -> None:
        evaluator = WakeTriggerEvaluator(
            stuck=StuckDetector(stuck_seconds=60.0),
            guard=ActionRepetitionGuard(max_repeats=3, window_s=10.0),
        )
        frame = _make_frame()
        perception = _make_perception()
        fired = False
        for i in range(6):
            result = evaluator.on_perception_tick(
                perception,
                frame_bytes=frame,
                predicate_fired=False,
                action_executed=True,
                last_action_hash="hash_W",
                abort_triggered=False,
                ts_ns=i * NS,
            )
            if result.reason == "w2_stuck":
                fired = True
                assert result.payload["trigger"] == "action_repetition_guard"
                break
        assert fired


class TestW3Abort:
    def test_fires_on_abort_condition(self) -> None:
        evaluator = WakeTriggerEvaluator(
            stuck=StuckDetector(),
            guard=ActionRepetitionGuard(),
        )
        result = evaluator.on_perception_tick(
            _make_perception(),
            frame_bytes=_make_frame(),
            predicate_fired=False,
            action_executed=False,
            last_action_hash=None,
            abort_triggered=True,
            ts_ns=0,
        )
        assert result.reason == "w3_abort"


class TestW5Verify:
    def test_fires_on_success_candidate(self) -> None:
        evaluator = WakeTriggerEvaluator(
            stuck=StuckDetector(),
            guard=ActionRepetitionGuard(),
        )
        result = evaluator.on_success_candidate(_make_perception(frame_id="victory"))
        assert result.reason == "w5_verify"
        assert result.payload["frame_id"] == "victory"


class TestNoWake:
    def test_normal_tick_no_wake(self) -> None:
        evaluator = WakeTriggerEvaluator(
            stuck=StuckDetector(stuck_seconds=60.0),
            guard=ActionRepetitionGuard(),
        )
        result = evaluator.on_perception_tick(
            _make_perception(),
            frame_bytes=_make_noisy_frame(),
            predicate_fired=False,
            action_executed=True,
            last_action_hash="hash_A",
            abort_triggered=False,
            ts_ns=0,
        )
        assert result.reason is None


def _make_noisy_frame() -> bytes:
    img = Image.new("RGB", (64, 64), (0, 0, 0))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = ((x * 17 + y * 31) % 256, (x * 7 + y * 23) % 256, (x * 11 + y * 13) % 256)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()
