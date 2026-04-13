"""FSM — top-level state machine for the agent cognitive architecture.

7 states, event-driven transitions. FSM manages "what class of thing
the agent is doing" (navigating, harvesting, etc). Internal behavior
within each state is delegated to Behavior Trees (Step 4-5).

State transitions are triggered by Blackboard events and wake triggers.
FSM reads Blackboard, never writes motor commands directly.

Aligned with final-design.md §1.4 W1-W5 wake triggers and §1.7
Backend Absence Recovery (DEGRADED state).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto


class State(Enum):
    IDLE = auto()
    PLANNING = auto()
    NAVIGATING = auto()
    HARVESTING = auto()
    INVENTORY = auto()
    RECOVERING = auto()
    DEGRADED = auto()


@dataclass
class Transition:
    source: State
    target: State
    trigger: str
    description: str


TRANSITIONS: list[Transition] = [
    Transition(State.IDLE, State.PLANNING, "session_start", "W1: new task received"),
    Transition(
        State.PLANNING,
        State.NAVIGATING,
        "plan_ready_navigate",
        "Brain returned plan, first subgoal needs movement",
    ),
    Transition(
        State.PLANNING,
        State.HARVESTING,
        "plan_ready_harvest",
        "Brain returned plan, already at target",
    ),
    Transition(
        State.PLANNING,
        State.INVENTORY,
        "plan_ready_inventory",
        "Brain returned plan, subgoal is crafting",
    ),
    Transition(State.NAVIGATING, State.HARVESTING, "target_reached", "Arrived at target location"),
    Transition(State.NAVIGATING, State.PLANNING, "w2_stuck", "W2: stuck while navigating → replan"),
    Transition(
        State.HARVESTING,
        State.NAVIGATING,
        "resource_exhausted",
        "Current location depleted, move to next",
    ),
    Transition(
        State.HARVESTING, State.INVENTORY, "harvest_complete_craft", "Harvest done, need to craft"
    ),
    Transition(State.HARVESTING, State.PLANNING, "w2_stuck", "W2: stuck while harvesting → replan"),
    Transition(
        State.INVENTORY,
        State.NAVIGATING,
        "craft_done_navigate",
        "Crafting complete, continue to next subgoal",
    ),
    Transition(State.INVENTORY, State.PLANNING, "craft_failed", "Crafting failed → replan"),
    Transition(
        State.RECOVERING,
        State.PLANNING,
        "danger_cleared",
        "Danger passed → replan from current state",
    ),
]

_GLOBAL_TRIGGERS: dict[str, State] = {
    "w3_abort": State.RECOVERING,
    "perception_unavailable": State.DEGRADED,
    "w5_pass": State.IDLE,
    "session_abort": State.IDLE,
}


def _log(msg: str) -> None:
    print(f"[gamemind fsm] {msg}", flush=True)


class FSM:
    """Event-driven finite state machine.

    Usage:
        fsm = FSM()
        fsm.transition("session_start")  # IDLE → PLANNING
        fsm.transition("plan_ready_navigate")  # PLANNING → NAVIGATING
    """

    def __init__(self) -> None:
        self._state = State.IDLE
        self._prev_state: State | None = None
        self._transition_count = 0
        self._degraded_context: State | None = None
        self._transition_table = self._build_table()
        self.on_transition: Callable[[State, State, str], None] | None = None

    @property
    def state(self) -> State:
        return self._state

    @property
    def prev_state(self) -> State | None:
        return self._prev_state

    @property
    def transition_count(self) -> int:
        return self._transition_count

    def transition(self, trigger: str) -> bool:
        if trigger in _GLOBAL_TRIGGERS:
            target = _GLOBAL_TRIGGERS[trigger]
            if target == State.DEGRADED:
                self._degraded_context = self._state
            return self._do_transition(target, trigger)

        if trigger == "perception_restored":
            return self._restore_from_degraded()

        key = (self._state, trigger)
        target = self._transition_table.get(key)
        if target is None:
            return False
        return self._do_transition(target, trigger)

    def can_transition(self, trigger: str) -> bool:
        if trigger in _GLOBAL_TRIGGERS:
            return True
        if trigger == "perception_restored" and self._state == State.DEGRADED:
            return True
        return (self._state, trigger) in self._transition_table

    def reset(self) -> None:
        self._state = State.IDLE
        self._prev_state = None
        self._transition_count = 0
        self._degraded_context = None

    def _do_transition(self, target: State, trigger: str) -> bool:
        if target == self._state:
            return False
        old = self._state
        _log(f"{old.name} → {target.name} (trigger: {trigger})")
        self._prev_state = old
        self._state = target
        self._transition_count += 1
        if self.on_transition is not None:
            self.on_transition(old, target, trigger)
        return True

    def _restore_from_degraded(self) -> bool:
        if self._state != State.DEGRADED:
            return False
        restore_to = self._degraded_context or State.PLANNING
        self._degraded_context = None
        _log(f"DEGRADED → {restore_to.name} (perception restored)")
        self._prev_state = self._state
        self._state = restore_to
        self._transition_count += 1
        return True

    def _build_table(self) -> dict[tuple[State, str], State]:
        table: dict[tuple[State, str], State] = {}
        for t in TRANSITIONS:
            table[(t.source, t.trigger)] = t.target
        return table
