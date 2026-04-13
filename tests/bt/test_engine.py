"""BT engine tests — core node types."""

from __future__ import annotations

from gamemind.blackboard import Blackboard
from gamemind.bt.engine import (
    Action,
    Condition,
    ForceSuccess,
    Inverter,
    Selector,
    Sequence,
    Status,
)
from gamemind.bt.motor_command import MotorCommand


def _true_condition(name: str = "true") -> Condition:
    return Condition(name, lambda _bb: True)


def _false_condition(name: str = "false") -> Condition:
    return Condition(name, lambda _bb: False)


def _success_action(name: str = "ok") -> Action:
    return Action(name, lambda _bb: (Status.SUCCESS, MotorCommand.tap("forward")))


def _running_action(name: str = "running") -> Action:
    return Action(name, lambda _bb: (Status.RUNNING, MotorCommand.hold("attack")))


def _failure_action(name: str = "fail") -> Action:
    return Action(name, lambda _bb: (Status.FAILURE, None))


class TestCondition:
    def test_true_returns_success(self) -> None:
        bb = Blackboard()
        assert _true_condition().tick(bb) == Status.SUCCESS

    def test_false_returns_failure(self) -> None:
        bb = Blackboard()
        assert _false_condition().tick(bb) == Status.FAILURE

    def test_reads_blackboard(self) -> None:
        bb = Blackboard()
        bb.write("crosshair_block", "oak_log")
        bb.swap()
        cond = Condition("check_log", lambda b: b.read_value("crosshair_block") == "oak_log")
        assert cond.tick(bb) == Status.SUCCESS


class TestAction:
    def test_produces_motor_command(self) -> None:
        bb = Blackboard()
        action = _success_action()
        status = action.tick(bb)
        assert status == Status.SUCCESS
        assert action.motor_command is not None
        assert action.motor_command.action_name == "forward"

    def test_running_action(self) -> None:
        bb = Blackboard()
        action = _running_action()
        assert action.tick(bb) == Status.RUNNING
        assert action.motor_command is not None


class TestSequence:
    def test_all_succeed(self) -> None:
        bb = Blackboard()
        seq = Sequence("s", [_true_condition(), _success_action()])
        assert seq.tick(bb) == Status.SUCCESS

    def test_first_fails_aborts(self) -> None:
        bb = Blackboard()
        seq = Sequence("s", [_false_condition(), _success_action()])
        assert seq.tick(bb) == Status.FAILURE

    def test_running_pauses(self) -> None:
        bb = Blackboard()
        seq = Sequence("s", [_true_condition(), _running_action()])
        assert seq.tick(bb) == Status.RUNNING

    def test_propagates_motor_command(self) -> None:
        bb = Blackboard()
        seq = Sequence("s", [_true_condition(), _success_action()])
        seq.tick(bb)
        assert seq.motor_command is not None


class TestSelector:
    def test_first_succeeds(self) -> None:
        bb = Blackboard()
        sel = Selector("s", [_true_condition(), _false_condition()])
        assert sel.tick(bb) == Status.SUCCESS

    def test_fallback_to_second(self) -> None:
        bb = Blackboard()
        sel = Selector("s", [_false_condition(), _success_action()])
        assert sel.tick(bb) == Status.SUCCESS

    def test_all_fail(self) -> None:
        bb = Blackboard()
        sel = Selector("s", [_false_condition(), _failure_action()])
        assert sel.tick(bb) == Status.FAILURE


class TestInverter:
    def test_inverts_success(self) -> None:
        bb = Blackboard()
        inv = Inverter("inv", _true_condition())
        assert inv.tick(bb) == Status.FAILURE

    def test_inverts_failure(self) -> None:
        bb = Blackboard()
        inv = Inverter("inv", _false_condition())
        assert inv.tick(bb) == Status.SUCCESS

    def test_running_passes_through(self) -> None:
        bb = Blackboard()
        inv = Inverter("inv", _running_action())
        assert inv.tick(bb) == Status.RUNNING


class TestForceSuccess:
    def test_forces_failure_to_success(self) -> None:
        bb = Blackboard()
        fs = ForceSuccess("fs", _failure_action())
        assert fs.tick(bb) == Status.SUCCESS
