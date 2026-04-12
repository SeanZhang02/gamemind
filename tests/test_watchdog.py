"""Watchdog tests — synthetic frames for each alert level."""

from __future__ import annotations

import io

from PIL import Image

from gamemind.blackboard import Blackboard
from gamemind.watchdog import AlertLevel, Watchdog


def _make_frame(color: tuple[int, int, int], w: int = 320, h: int = 180) -> bytes:
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _make_noisy_frame(base: tuple[int, int, int], noise: int = 30) -> bytes:
    img = Image.new("RGB", (320, 180), base)
    px = img.load()
    for x in range(320):
        for y in range(180):
            offset = ((x * 17 + y * 31) % (noise * 2)) - noise
            r = max(0, min(255, base[0] + offset))
            g = max(0, min(255, base[1] + offset // 2))
            b = max(0, min(255, base[2] - offset))
            px[x, y] = (r, g, b)
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


class TestFrameDiff:
    def test_identical_frames_low_diff(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        frame = _make_frame((100, 100, 100))
        wd.check(frame)
        alerts = wd.check(frame)
        result = bb.read("frame_diff_score")
        assert result is not None
        assert result.value < 2.0
        assert not any(a.level >= AlertLevel.WARNING for a in alerts)

    def test_different_frames_high_diff(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        wd.check(_make_frame((50, 50, 50)))
        wd.check(_make_frame((200, 200, 200)))
        result = bb.read("frame_diff_score")
        assert result is not None
        assert result.value > 10.0


class TestStuckWall:
    def test_detects_stuck_when_moving(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        wd.set_motor_moving(True)
        frame = _make_frame((80, 80, 80))
        stuck_fired = False
        for _ in range(20):
            alerts = wd.check(frame)
            if any(a.signal == "stuck_wall" for a in alerts):
                stuck_fired = True
                break
        assert stuck_fired

    def test_no_stuck_when_not_moving(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        wd.set_motor_moving(False)
        frame = _make_frame((80, 80, 80))
        for _ in range(20):
            alerts = wd.check(frame)
            assert not any(a.signal == "stuck_wall" for a in alerts)


class TestScreenBlack:
    def test_detects_black_screen(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        frame = _make_frame((2, 2, 2))
        black_fired = False
        for _ in range(10):
            alerts = wd.check(frame)
            if any(a.signal == "screen_black" for a in alerts):
                black_fired = True
                assert any(a.level == AlertLevel.FATAL for a in alerts)
                break
        assert black_fired
        assert wd.is_frozen

    def test_normal_brightness_no_black(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        frame = _make_frame((100, 100, 100))
        for _ in range(10):
            alerts = wd.check(frame)
            assert not any(a.signal == "screen_black" for a in alerts)


class TestScreenRed:
    def test_detects_red_screen(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        frame = _make_frame((220, 30, 20))
        red_fired = False
        for _ in range(5):
            alerts = wd.check(frame)
            if any(a.signal == "screen_red" for a in alerts):
                red_fired = True
                assert any(a.level == AlertLevel.EMERGENCY for a in alerts)
                break
        assert red_fired
        assert wd.is_emergency

    def test_non_red_no_alert(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        frame = _make_frame((30, 220, 30))
        for _ in range(5):
            alerts = wd.check(frame)
            assert not any(a.signal == "screen_red" for a in alerts)


class TestFreezeTimeout:
    def test_detects_freeze(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        wd.set_motor_moving(False)
        frame = _make_frame((100, 100, 100))
        freeze_fired = False
        for _ in range(100):
            alerts = wd.check(frame)
            if any(a.signal == "freeze_timeout" for a in alerts):
                freeze_fired = True
                break
        assert freeze_fired
        assert wd.is_frozen


class TestReset:
    def test_reset_clears_state(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        wd.set_motor_moving(True)
        frame = _make_frame((80, 80, 80))
        for _ in range(20):
            wd.check(frame)
        wd.reset()
        alerts = wd.check(frame)
        assert not any(a.signal == "stuck_wall" for a in alerts)

    def test_clear_emergency(self) -> None:
        bb = Blackboard()
        wd = Watchdog(bb)
        frame = _make_frame((220, 30, 20))
        for _ in range(5):
            wd.check(frame)
        assert wd.is_emergency
        wd.clear_emergency()
        assert not wd.is_emergency
