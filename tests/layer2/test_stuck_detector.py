"""Three synthetic fixtures per §1.4 W2 spec:

(a) Static inventory UI during active play (motion-quiet BUT predicates
    fire → NOT stuck)
(b) Character staring at wall with no input (all three conditions hold
    for >stuck_seconds → stuck)
(c) High-particle combat (motion metric > entropy_floor → NOT stuck
    from entropy alone)
"""

from __future__ import annotations

import io

from PIL import Image

from gamemind.layer2.stuck_detector import StuckDetector, _motion_metric


def _make_frame(color: tuple[int, int, int], w: int = 64, h: int = 64) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _make_noisy_frame(base: tuple[int, int, int], noise: int, w: int = 64, h: int = 64) -> bytes:
    img = Image.new("RGB", (w, h), base)
    px = img.load()
    for x in range(w):
        for y in range(h):
            offset = ((x * 7 + y * 13) % (noise * 2)) - noise
            r = max(0, min(255, base[0] + offset))
            g = max(0, min(255, base[1] - offset))
            b = max(0, min(255, base[2] + offset // 2))
            px[x, y] = (r, g, b)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


NS = 1_000_000_000


def test_motion_metric_identical_frames() -> None:
    a = list(range(256)) * 16
    assert _motion_metric(a, a) == 0.0


def test_motion_metric_opposite_frames() -> None:
    a = [0] * 4096
    b = [255] * 4096
    assert _motion_metric(a, b) == 1.0


class TestStaticUIWithPredicates:
    """Motion-quiet but predicates fire → NOT stuck."""

    def test_not_stuck_when_predicates_fire(self) -> None:
        detector = StuckDetector(stuck_seconds=5.0, entropy_floor=0.02)
        frame = _make_frame((100, 100, 100))
        for i in range(20):
            ts = i * NS
            result = detector.update(
                frame_bytes=frame,
                predicate_fired=(i % 3 == 0),
                action_executed=False,
                ts_ns=ts,
            )
            assert not result.is_stuck, f"tick {i} should NOT be stuck (predicates firing)"


class TestStaringAtWall:
    """All three conditions hold → stuck after stuck_seconds."""

    def test_stuck_after_window(self) -> None:
        detector = StuckDetector(stuck_seconds=3.0, entropy_floor=0.02, lookback_seconds=1.0)
        frame = _make_frame((80, 80, 80))
        stuck_fired = False
        for i in range(10):
            ts = i * NS
            result = detector.update(
                frame_bytes=frame,
                predicate_fired=False,
                action_executed=False,
                ts_ns=ts,
            )
            if result.is_stuck:
                stuck_fired = True
                assert result.reason == "motion_quiet_no_predicate_no_action"
                break
        assert stuck_fired, "stuck should have fired within 10 ticks at 1Hz with 3s window"

    def test_not_stuck_before_window(self) -> None:
        detector = StuckDetector(stuck_seconds=10.0, entropy_floor=0.02, lookback_seconds=1.0)
        frame = _make_frame((80, 80, 80))
        for i in range(5):
            ts = i * NS
            result = detector.update(
                frame_bytes=frame,
                predicate_fired=False,
                action_executed=False,
                ts_ns=ts,
            )
            assert not result.is_stuck, f"tick {i}: should not be stuck before 10s window"


class TestHighMotion:
    """High visual entropy → NOT stuck regardless of predicates/actions."""

    def test_not_stuck_with_motion(self) -> None:
        detector = StuckDetector(stuck_seconds=2.0, entropy_floor=0.02, lookback_seconds=0.5)
        for i in range(10):
            ts = i * NS
            frame = _make_noisy_frame((50 + i * 15, 100, 100), noise=60 + i * 5)
            result = detector.update(
                frame_bytes=frame,
                predicate_fired=False,
                action_executed=False,
                ts_ns=ts,
            )
            assert not result.is_stuck, f"tick {i}: motion should prevent stuck"


class TestReset:
    def test_reset_clears_state(self) -> None:
        detector = StuckDetector(stuck_seconds=2.0, entropy_floor=0.02, lookback_seconds=0.5)
        frame = _make_frame((80, 80, 80))
        for i in range(5):
            detector.update(
                frame_bytes=frame, predicate_fired=False, action_executed=False, ts_ns=i * NS
            )
        detector.reset()
        result = detector.update(
            frame_bytes=frame, predicate_fired=False, action_executed=False, ts_ns=10 * NS
        )
        assert not result.is_stuck, "reset should clear stuck window"
