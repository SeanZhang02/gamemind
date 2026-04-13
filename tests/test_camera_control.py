"""Tests for camera control (mouse relative movement) — MOUSE_MOVE command type."""

from __future__ import annotations

from gamemind.bt.motor_command import MotorCommand, MotorCommandType
from gamemind.motor import Motor, ResolvedCommand


ACTIONS = {
    "forward": "W",
    "attack": "MouseLeft",
    "look_up": "mouse_rel:0,-80",
    "look_down": "mouse_rel:0,80",
    "turn_left": "mouse_rel:-150,0",
    "turn_right": "mouse_rel:150,0",
}


class TestMotorResolvesMouseRel:
    def test_motor_resolves_mouse_rel_key(self) -> None:
        """Motor.resolve with mouse_rel key returns MOUSE_MOVE type."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("turn_left")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.command_type == MotorCommandType.MOUSE_MOVE
        assert result.reason == "bt"

    def test_resolved_command_has_dx_dy_turn_left(self) -> None:
        """Check dx/dy values parsed correctly for turn_left."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("turn_left")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.dx == -150
        assert result.dy == 0

    def test_resolved_command_has_dx_dy_look_up(self) -> None:
        """Check dx/dy values parsed correctly for look_up."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("look_up")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.dx == 0
        assert result.dy == -80

    def test_resolved_command_has_dx_dy_look_down(self) -> None:
        """Check dx/dy values parsed correctly for look_down."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("look_down")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.dx == 0
        assert result.dy == 80

    def test_resolved_command_has_dx_dy_turn_right(self) -> None:
        """Check dx/dy values parsed correctly for turn_right."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("turn_right")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.dx == 150
        assert result.dy == 0


class TestMouseMoveDoesNotHold:
    def test_mouse_move_does_not_set_is_holding(self) -> None:
        """MOUSE_MOVE should not set is_holding on motor state (no key to hold)."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("turn_left")
        motor.resolve(cmd)
        # MOUSE_MOVE branch skips setting is_holding
        assert not motor._state.is_holding

    def test_mouse_move_does_not_affect_held_keys_tracking(self) -> None:
        """MOUSE_MOVE resolved key ('mouse_rel:...') should not be added to
        _held_keys in the runner. We verify via ResolvedCommand fields only:
        command_type is MOUSE_MOVE, so the runner's HOLD branch won't execute."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("turn_left")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.command_type == MotorCommandType.MOUSE_MOVE
        # The runner handles MOUSE_MOVE in its own branch (no key_down/key_up)


class TestMouseMoveFactory:
    def test_mouse_move_factory(self) -> None:
        """MotorCommand.mouse_move() factory produces correct type."""
        cmd = MotorCommand.mouse_move("turn_left")
        assert cmd.command_type == MotorCommandType.MOUSE_MOVE
        assert cmd.action_name == "turn_left"

    def test_regular_actions_still_work(self) -> None:
        """Non-mouse_rel actions still resolve normally."""
        motor = Motor(ACTIONS)
        motor._state.is_idle = False
        cmd = MotorCommand.hold("forward")
        result = motor.resolve(cmd)
        assert result is not None
        assert result.command_type == MotorCommandType.HOLD
        assert result.key == "W"
        assert result.dx == 0
        assert result.dy == 0


class TestResolvedCommandDefaults:
    def test_dx_dy_default_zero(self) -> None:
        """ResolvedCommand defaults dx=0, dy=0 for non-mouse commands."""
        rc = ResolvedCommand(
            action="forward",
            key="W",
            command_type=MotorCommandType.HOLD,
            reason="bt",
        )
        assert rc.dx == 0
        assert rc.dy == 0
