"""Tests for motor.py — staleness, hysteresis, priority chain."""

from __future__ import annotations

import time

from gamemind.bt.motor_command import MotorCommand, MotorCommandType
from gamemind.motor import Motor


ACTIONS = {"forward": "W", "attack": "MouseLeft", "jump": "Space"}


class TestBasicResolve:
    def test_resolves_tap(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.tap("forward")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.key == "W"
        assert result.command_type == MotorCommandType.TAP

    def test_resolves_hold(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("attack", duration_ms=500.0)
        result = motor.resolve(cmd)
        assert result is not None
        assert result.key == "MouseLeft"
        assert result.duration_ms == 500.0

    def test_unknown_action_returns_none(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.tap("nonexistent")
        assert motor.resolve(cmd) is None


class TestStaleness:
    def test_idle_after_timeout(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor._state.last_command_ns = time.monotonic_ns() - 900_000_000
        motor.resolve(None)
        assert motor._state.is_idle


class TestHysteresis:
    def test_needs_two_consecutive_to_resume_after_staleness(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.resolve(MotorCommand.tap("forward"))
        import time

        motor._state.last_command_ns = time.monotonic_ns() - 900_000_000
        motor.resolve(None)
        assert motor._state.is_idle
        motor._state.recovery_streak = 0
        cmd = MotorCommand.tap("forward")
        assert motor.resolve(cmd) is None
        result = motor.resolve(cmd)
        assert result is not None
        assert result.key == "W"

    def test_first_boot_skips_hysteresis(self) -> None:
        motor = Motor(ACTIONS)
        cmd = MotorCommand.tap("forward")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.key == "W"


class TestPriorityChain:
    def test_freeze_overrides_everything(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.freeze()
        cmd = MotorCommand.tap("forward")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.reason == "frozen"
        assert result.command_type == MotorCommandType.IDLE

    def test_emergency_overrides_bt(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.set_emergency(MotorCommand.hold("jump", duration_ms=200.0))
        cmd = MotorCommand.tap("forward")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.reason == "emergency"
        assert result.action == "jump"

    def test_clear_emergency_resumes_bt(self) -> None:
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.set_emergency(MotorCommand.tap("jump"))
        motor.clear_emergency()
        cmd = MotorCommand.tap("forward")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.reason == "bt"
        assert result.key == "W"


class TestReset:
    def test_reset_clears_all(self) -> None:
        motor = Motor(ACTIONS)
        motor.freeze()
        motor.set_emergency(MotorCommand.tap("jump"))
        motor.reset()
        assert not motor.is_frozen
        assert motor._state.is_idle
