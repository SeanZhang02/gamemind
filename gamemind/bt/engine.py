"""BT engine — core node types for the behavior tree.

All nodes implement tick(blackboard) -> Status. Composite nodes
(Sequence, Selector) manage children. Leaf nodes (Condition, Action)
interact with Blackboard and produce MotorCommands.

Tick is event-driven: only called when new perception arrives or
an internal timer fires. Not called every game frame.
"""

from __future__ import annotations

from enum import Enum, auto
from collections.abc import Callable

from gamemind.blackboard import Blackboard
from gamemind.bt.motor_command import MotorCommand


class Status(Enum):
    SUCCESS = auto()
    FAILURE = auto()
    RUNNING = auto()


class Node:
    """Base class for all BT nodes."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.last_status: Status | None = None
        self.motor_command: MotorCommand | None = None

    def tick(self, bb: Blackboard) -> Status:
        raise NotImplementedError

    def reset(self) -> None:
        self.last_status = None
        self.motor_command = None


class Sequence(Node):
    """Execute children in order. Fails on first FAILURE. Returns RUNNING
    if any child is RUNNING. Returns SUCCESS when all succeed."""

    def __init__(self, name: str, children: list[Node]) -> None:
        super().__init__(name)
        self.children = children
        self._current = 0

    def tick(self, bb: Blackboard) -> Status:
        while self._current < len(self.children):
            child = self.children[self._current]
            status = child.tick(bb)
            if child.motor_command is not None:
                self.motor_command = child.motor_command
            if status == Status.FAILURE:
                self._current = 0
                self.last_status = Status.FAILURE
                return Status.FAILURE
            if status == Status.RUNNING:
                self.last_status = Status.RUNNING
                return Status.RUNNING
            self._current += 1
        self._current = 0
        self.last_status = Status.SUCCESS
        return Status.SUCCESS

    def reset(self) -> None:
        super().reset()
        self._current = 0
        for child in self.children:
            child.reset()


class Selector(Node):
    """Try children in order. Returns SUCCESS on first success. Returns
    FAILURE only when all children fail."""

    def __init__(self, name: str, children: list[Node]) -> None:
        super().__init__(name)
        self.children = children
        self._current = 0

    def tick(self, bb: Blackboard) -> Status:
        while self._current < len(self.children):
            child = self.children[self._current]
            status = child.tick(bb)
            if child.motor_command is not None:
                self.motor_command = child.motor_command
            if status == Status.SUCCESS:
                self._current = 0
                self.last_status = Status.SUCCESS
                return Status.SUCCESS
            if status == Status.RUNNING:
                self.last_status = Status.RUNNING
                return Status.RUNNING
            self._current += 1
        self._current = 0
        self.last_status = Status.FAILURE
        return Status.FAILURE

    def reset(self) -> None:
        super().reset()
        self._current = 0
        for child in self.children:
            child.reset()


class Condition(Node):
    """Leaf node: reads Blackboard and returns SUCCESS/FAILURE.

    check_fn receives the Blackboard and returns True (SUCCESS) or
    False (FAILURE). Never returns RUNNING.
    """

    def __init__(self, name: str, check_fn: Callable[[Blackboard], bool]) -> None:
        super().__init__(name)
        self._check = check_fn

    def tick(self, bb: Blackboard) -> Status:
        result = self._check(bb)
        self.last_status = Status.SUCCESS if result else Status.FAILURE
        return self.last_status


class Action(Node):
    """Leaf node: produces a MotorCommand and returns a status.

    action_fn receives the Blackboard, returns (Status, MotorCommand | None).
    Use Status.RUNNING for ongoing actions, SUCCESS when complete.
    """

    def __init__(
        self,
        name: str,
        action_fn: Callable[[Blackboard], tuple[Status, MotorCommand | None]],
    ) -> None:
        super().__init__(name)
        self._action = action_fn

    def tick(self, bb: Blackboard) -> Status:
        status, cmd = self._action(bb)
        self.motor_command = cmd
        self.last_status = status
        return status


class Inverter(Node):
    """Decorator: inverts child's SUCCESS↔FAILURE. RUNNING passes through."""

    def __init__(self, name: str, child: Node) -> None:
        super().__init__(name)
        self.child = child

    def tick(self, bb: Blackboard) -> Status:
        status = self.child.tick(bb)
        self.motor_command = self.child.motor_command
        if status == Status.SUCCESS:
            self.last_status = Status.FAILURE
            return Status.FAILURE
        if status == Status.FAILURE:
            self.last_status = Status.SUCCESS
            return Status.SUCCESS
        self.last_status = Status.RUNNING
        return Status.RUNNING

    def reset(self) -> None:
        super().reset()
        self.child.reset()


class ForceSuccess(Node):
    """Decorator: always returns SUCCESS regardless of child result."""

    def __init__(self, name: str, child: Node) -> None:
        super().__init__(name)
        self.child = child

    def tick(self, bb: Blackboard) -> Status:
        self.child.tick(bb)
        self.motor_command = self.child.motor_command
        self.last_status = Status.SUCCESS
        return Status.SUCCESS

    def reset(self) -> None:
        super().reset()
        self.child.reset()
