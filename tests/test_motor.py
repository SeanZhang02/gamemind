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
        motor._state.last_command_ns = time.monotonic_ns() - 11_000_000_000  # >10000ms staleness
        motor.resolve(None)
        assert motor._state.is_idle


class TestHysteresis:
    def test_resumes_on_first_valid_after_staleness(self) -> None:
        """Recovery threshold is 1 — resumes immediately on first valid command."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.resolve(MotorCommand.tap("forward"))
        import time

        motor._state.last_command_ns = time.monotonic_ns() - 11_000_000_000  # >10000ms staleness
        motor.resolve(None)
        assert motor._state.is_idle
        motor._state.recovery_streak = 0
        cmd = MotorCommand.tap("forward")
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


class TestEmergencyExpiry:
    def test_emergency_clears_after_duration(self) -> None:
        """Emergency with duration_ms>0 expires after that duration."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.set_emergency(MotorCommand.hold("forward", duration_ms=500.0))
        # Simulate 600ms elapsed by backdating the set time
        motor._emergency_set_ns = time.monotonic_ns() - 600_000_000  # 600ms ago
        cmd = MotorCommand.tap("attack")
        result = motor.resolve(cmd)
        # Emergency expired, should fall through to normal BT command
        assert result is not None
        assert result.reason == "bt"
        assert result.action == "attack"
        assert motor._emergency_command is None

    def test_emergency_single_tick_clears(self) -> None:
        """Emergency with duration_ms=0 clears after one resolve call."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.set_emergency(MotorCommand.hold("forward", duration_ms=0.0))
        # First resolve: returns emergency
        result = motor.resolve(MotorCommand.tap("attack"))
        assert result is not None
        assert result.reason == "emergency"
        assert result.action == "forward"
        # Emergency should now be cleared
        assert motor._emergency_command is None
        # Second resolve: normal BT command
        result2 = motor.resolve(MotorCommand.tap("attack"))
        assert result2 is not None
        assert result2.reason == "bt"
        assert result2.action == "attack"

    def test_emergency_persists_within_duration(self) -> None:
        """Emergency with duration_ms>0 persists until duration expires."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        motor.set_emergency(MotorCommand.hold("forward", duration_ms=500.0))
        # Simulate only 200ms elapsed
        motor._emergency_set_ns = time.monotonic_ns() - 200_000_000  # 200ms ago
        cmd = MotorCommand.tap("attack")
        result = motor.resolve(cmd)
        # Emergency still active
        assert result is not None
        assert result.reason == "emergency"
        assert result.action == "forward"
        assert motor._emergency_command is not None


class TestReset:
    def test_reset_clears_all(self) -> None:
        motor = Motor(ACTIONS)
        motor.freeze()
        motor.set_emergency(MotorCommand.tap("jump"))
        motor.reset()
        assert not motor.is_frozen
        assert motor._state.is_idle
