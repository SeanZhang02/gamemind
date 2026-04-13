"""BT decorator tests — Timeout, Cooldown, ConfidenceGate, ReactiveSelector."""

from __future__ import annotations

import time

from gamemind.blackboard import Blackboard
from gamemind.bt.decorators import ConfidenceGate, Cooldown, ReactiveSelector, Timeout
from gamemind.bt.engine import Action, Condition, Sequence, Status
from gamemind.bt.motor_command import MotorCommand


def _true_cond() -> Condition:
    return Condition("t", lambda _: True)


def _false_cond() -> Condition:
    return Condition("f", lambda _: False)


def _running_action() -> Action:
    return Action("run", lambda _: (Status.RUNNING, MotorCommand.hold("attack")))


def _success_action() -> Action:
    return Action("ok", lambda _: (Status.SUCCESS, MotorCommand.tap("forward")))


class TestTimeout:
    def test_child_succeeds_within_timeout(self) -> None:
        bb = Blackboard()
        to = Timeout("to", _success_action(), timeout_ms=5000.0)
        assert to.tick(bb) == Status.SUCCESS

    def test_timeout_fires(self) -> None:
        bb = Blackboard()
        to = Timeout("to", _running_action(), timeout_ms=50.0)
        to.tick(bb)
        time.sleep(0.06)
        assert to.tick(bb) == Status.FAILURE

    def test_reset_clears_timer(self) -> None:
        bb = Blackboard()
        to = Timeout("to", _running_action(), timeout_ms=50.0)
        to.tick(bb)
        to.reset()
        assert to.tick(bb) == Status.RUNNING


class TestCooldown:
    def test_blocks_during_cooldown(self) -> None:
        bb = Blackboard()
        cd = Cooldown("cd", _success_action(), cooldown_ms=100.0)
        assert cd.tick(bb) == Status.SUCCESS
        assert cd.tick(bb) == Status.FAILURE

    def test_allows_after_cooldown(self) -> None:
        bb = Blackboard()
        cd = Cooldown("cd", _success_action(), cooldown_ms=30.0)
        cd.tick(bb)
        time.sleep(0.04)
        assert cd.tick(bb) == Status.SUCCESS


class TestConfidenceGate:
    def test_requires_consecutive_successes(self) -> None:
        bb = Blackboard()
        gate = ConfidenceGate("cg", _true_cond(), required_frames=3)
        assert gate.tick(bb) == Status.RUNNING
        assert gate.tick(bb) == Status.RUNNING
        assert gate.tick(bb) == Status.SUCCESS

    def test_failure_resets_streak(self) -> None:
        call_count = [0]

        def alternating(bb: Blackboard) -> bool:
            call_count[0] += 1
            return call_count[0] != 2

        cond = Condition("alt", alternating)
        gate = ConfidenceGate("cg", cond, required_frames=3)
        bb = Blackboard()
        gate.tick(bb)  # True → streak 1
        gate.tick(bb)  # False → reset
        gate.tick(bb)  # True → streak 1
        gate.tick(bb)  # True → streak 2
        assert gate.tick(bb) == Status.SUCCESS  # True → streak 3


class TestReactiveSelector:
    def test_always_starts_from_first(self) -> None:
        bb = Blackboard()
        calls: list[str] = []

        def track(name: str, result: bool):
            def fn(_bb: Blackboard) -> bool:
                calls.append(name)
                return result

            return fn

        rs = ReactiveSelector(
            "rs",
            [
                Condition("high", track("high", False)),
                Condition("low", track("low", True)),
            ],
        )
        rs.tick(bb)
        rs.tick(bb)
        assert calls == ["high", "low", "high", "low"]

    def test_high_priority_preempts_running(self) -> None:
        bb = Blackboard()
        tick_count = [0]

        def emergency(bb: Blackboard) -> bool:
            tick_count[0] += 1
            return tick_count[0] >= 3

        rs = ReactiveSelector(
            "rs",
            [
                Sequence("emerg", [Condition("check", emergency), _success_action()]),
                _running_action(),
            ],
        )
        assert rs.tick(bb) == Status.RUNNING
        assert rs.tick(bb) == Status.RUNNING
        assert rs.tick(bb) == Status.SUCCESS
