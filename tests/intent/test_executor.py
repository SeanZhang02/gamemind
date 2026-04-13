"""Tests for IntentExecutor — decision matrix coverage.

24 tests covering every cell in the (intent x facing) decision matrix,
plus edge cases for unknown intents and invalid adapter actions.
"""

from __future__ import annotations

import pytest

from gamemind.bt.motor_command import MotorCommandType
from gamemind.intent.executor import IntentExecutor
from gamemind.intent.models import Intent, IntentType

# Standard Minecraft adapter actions for testing
_MINECRAFT_ACTIONS = {
    "forward": "w",
    "backward": "s",
    "turn_left": "mouse_left",
    "turn_right": "mouse_right",
    "look_up": "mouse_up",
    "look_down": "mouse_down",
    "attack": "left_click",
    "jump": "space",
}


def _make_executor(actions: dict[str, str] | None = None) -> IntentExecutor:
    return IntentExecutor(actions or _MINECRAFT_ACTIONS)


def _approach(target: str = "oak_tree") -> Intent:
    return Intent(IntentType.APPROACH, target_anchor=target)


def _attack(target: str = "zombie") -> Intent:
    return Intent(IntentType.ATTACK_TARGET, target_anchor=target)


def _look_around() -> Intent:
    return Intent(IntentType.LOOK_AROUND)


def _retreat() -> Intent:
    return Intent(IntentType.RETREAT)


# ── APPROACH tests ──────────────────────────────────────────────


class TestApproach:
    def test_approach_facing_down(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_down", "ahead")
        assert cmd.action_name == "look_up"
        assert cmd.command_type == MotorCommandType.TAP

    def test_approach_facing_horizon_target_ahead(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "ahead")
        assert cmd.action_name == "forward"
        assert cmd.command_type == MotorCommandType.HOLD

    def test_approach_facing_horizon_target_left(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "left")
        assert cmd.action_name == "turn_left"
        assert cmd.command_type == MotorCommandType.TAP

    def test_approach_facing_horizon_target_right(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "right")
        assert cmd.action_name == "turn_right"
        assert cmd.command_type == MotorCommandType.TAP

    def test_approach_facing_horizon_target_behind(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "behind")
        # Turn around — always turn left for consistency
        assert cmd.action_name == "turn_left"
        assert cmd.command_type == MotorCommandType.TAP

    def test_approach_facing_up(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_up", "ahead")
        assert cmd.action_name == "look_down"
        assert cmd.command_type == MotorCommandType.TAP


# ── ATTACK tests ────────────────────────────────────────────────


class TestAttack:
    def test_attack_facing_down(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_attack(), "", None, "looking_down", "ahead")
        assert cmd.action_name == "look_up"
        assert cmd.command_type == MotorCommandType.TAP

    def test_attack_facing_horizon_crosshair_matches(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_attack("zombie"), "", "zombie", "looking_at_horizon", "ahead")
        assert cmd.action_name == "attack"
        assert cmd.command_type == MotorCommandType.HOLD

    def test_attack_facing_horizon_crosshair_wrong(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_attack("zombie"), "", "dirt", "looking_at_horizon", "left")
        # Should orient toward target
        assert cmd.action_name == "turn_left"
        assert cmd.command_type == MotorCommandType.TAP

    def test_attack_facing_up(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_attack(), "", None, "looking_up", "ahead")
        assert cmd.action_name == "look_down"
        assert cmd.command_type == MotorCommandType.TAP


# ── LOOK_AROUND tests ──────────────────────────────────────────


class TestLookAround:
    def test_look_around_facing_down(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_look_around(), "", None, "looking_down")
        assert cmd.action_name == "look_up"
        assert cmd.command_type == MotorCommandType.TAP

    def test_look_around_facing_horizon(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_look_around(), "", None, "looking_at_horizon")
        assert cmd.action_name == "turn_left"
        assert cmd.command_type == MotorCommandType.TAP

    def test_look_around_facing_up(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_look_around(), "", None, "looking_up")
        assert cmd.action_name == "look_down"
        assert cmd.command_type == MotorCommandType.TAP


# ── RETREAT tests ───────────────────────────────────────────────


class TestRetreat:
    def test_retreat_facing_down(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_retreat(), "", None, "looking_down")
        assert cmd.action_name == "look_up"
        assert cmd.command_type == MotorCommandType.TAP

    def test_retreat_facing_horizon(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_retreat(), "", None, "looking_at_horizon")
        assert cmd.action_name == "backward"
        assert cmd.command_type == MotorCommandType.HOLD

    def test_retreat_facing_up(self) -> None:
        exe = _make_executor()
        cmd = exe.next_action(_retreat(), "", None, "looking_up")
        assert cmd.action_name == "look_down"
        assert cmd.command_type == MotorCommandType.TAP


# ── Edge case tests ─────────────────────────────────────────────


class TestEdgeCases:
    def test_unknown_intent_raises(self) -> None:
        """An intent with a truly unknown type should raise ValueError."""
        exe = _make_executor()
        intent = Intent(IntentType.APPROACH)
        # Monkey-patch to simulate an unknown intent type
        intent.intent_type = "totally_unknown"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="Unknown intent type"):
            exe.next_action(intent, "", None, "looking_at_horizon")

    def test_invalid_action_not_in_adapter(self) -> None:
        """When adapter doesn't have the needed action, fall back to idle."""
        # Adapter with only "forward" — no turn actions
        exe = _make_executor({"forward": "w"})
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "left")
        # turn_left not in adapter → should fallback to idle
        assert cmd.command_type == MotorCommandType.IDLE

    def test_approach_no_direction_info(self) -> None:
        """When no anchor direction available, scan by turning."""
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", None)
        assert cmd.action_name == "turn_left"
        assert cmd.command_type == MotorCommandType.TAP

    def test_approach_ahead_left_direction(self) -> None:
        """ahead_left should turn left."""
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "ahead_left")
        assert cmd.action_name == "turn_left"
        assert cmd.command_type == MotorCommandType.TAP

    def test_approach_ahead_right_direction(self) -> None:
        """ahead_right should turn right."""
        exe = _make_executor()
        cmd = exe.next_action(_approach(), "", None, "looking_at_horizon", "ahead_right")
        assert cmd.action_name == "turn_right"
        assert cmd.command_type == MotorCommandType.TAP

    def test_reset_clears_orient_step(self) -> None:
        exe = _make_executor()
        exe._orient_step = 5
        exe.reset()
        assert exe._orient_step == 0
