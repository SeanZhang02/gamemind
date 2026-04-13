"""FSM tests — state transitions, global triggers, degraded recovery."""

from __future__ import annotations

from gamemind.fsm import FSM, State


class TestBasicTransitions:
    def test_starts_idle(self) -> None:
        fsm = FSM()
        assert fsm.state == State.IDLE

    def test_session_start_to_planning(self) -> None:
        fsm = FSM()
        assert fsm.transition("session_start")
        assert fsm.state == State.PLANNING

    def test_planning_to_navigating(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        assert fsm.transition("plan_ready_navigate")
        assert fsm.state == State.NAVIGATING

    def test_planning_to_harvesting(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        assert fsm.transition("plan_ready_harvest")
        assert fsm.state == State.HARVESTING

    def test_navigating_to_harvesting(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_navigate")
        assert fsm.transition("target_reached")
        assert fsm.state == State.HARVESTING

    def test_harvesting_to_navigating(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_harvest")
        assert fsm.transition("resource_exhausted")
        assert fsm.state == State.NAVIGATING

    def test_invalid_trigger_returns_false(self) -> None:
        fsm = FSM()
        assert not fsm.transition("target_reached")
        assert fsm.state == State.IDLE


class TestW2Stuck:
    def test_navigating_stuck_replans(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_navigate")
        assert fsm.transition("w2_stuck")
        assert fsm.state == State.PLANNING

    def test_harvesting_stuck_replans(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_harvest")
        assert fsm.transition("w2_stuck")
        assert fsm.state == State.PLANNING


class TestGlobalTriggers:
    def test_w3_abort_from_any_state(self) -> None:
        for start_trigger in ["session_start", None]:
            fsm = FSM()
            if start_trigger:
                fsm.transition(start_trigger)
            fsm.transition("w3_abort")
            assert fsm.state == State.RECOVERING

    def test_w5_pass_returns_idle(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_harvest")
        assert fsm.transition("w5_pass")
        assert fsm.state == State.IDLE

    def test_session_abort_returns_idle(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        assert fsm.transition("session_abort")
        assert fsm.state == State.IDLE


class TestDegraded:
    def test_perception_unavailable_enters_degraded(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_navigate")
        assert fsm.transition("perception_unavailable")
        assert fsm.state == State.DEGRADED

    def test_perception_restored_returns_to_prev(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_navigate")
        fsm.transition("perception_unavailable")
        assert fsm.transition("perception_restored")
        assert fsm.state == State.NAVIGATING

    def test_perception_restored_from_idle_returns_to_idle(self) -> None:
        fsm = FSM()
        fsm.transition("perception_unavailable")
        assert fsm.transition("perception_restored")
        assert fsm.state == State.IDLE

    def test_perception_restored_only_from_degraded(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        assert not fsm.transition("perception_restored")


class TestRecovering:
    def test_recovering_to_planning(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("w3_abort")
        assert fsm.state == State.RECOVERING
        assert fsm.transition("danger_cleared")
        assert fsm.state == State.PLANNING


class TestMetadata:
    def test_transition_count(self) -> None:
        fsm = FSM()
        assert fsm.transition_count == 0
        fsm.transition("session_start")
        fsm.transition("plan_ready_navigate")
        assert fsm.transition_count == 2

    def test_prev_state(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.transition("plan_ready_harvest")
        assert fsm.prev_state == State.PLANNING

    def test_reset(self) -> None:
        fsm = FSM()
        fsm.transition("session_start")
        fsm.reset()
        assert fsm.state == State.IDLE
        assert fsm.transition_count == 0

    def test_can_transition(self) -> None:
        fsm = FSM()
        assert fsm.can_transition("session_start")
        assert not fsm.can_transition("target_reached")
        assert fsm.can_transition("w3_abort")
