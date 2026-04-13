"""BT decorators — Timeout, Cooldown, ConfidenceGate, ReactiveSelector.

These wrap child nodes with temporal or confidence-based guards to
prevent thrashing and filter VLM noise.
"""

from __future__ import annotations

import time

from gamemind.blackboard import Blackboard
from gamemind.bt.engine import Node, Status


class Timeout(Node):
    """Decorator: child must complete within timeout_ms or returns FAILURE."""

    def __init__(self, name: str, child: Node, timeout_ms: float) -> None:
        super().__init__(name)
        self.child = child
        self._timeout_ms = timeout_ms
        self._start_ns: int | None = None

    def tick(self, bb: Blackboard) -> Status:
        if self._start_ns is None:
            self._start_ns = time.monotonic_ns()

        elapsed_ms = (time.monotonic_ns() - self._start_ns) / 1_000_000.0
        if elapsed_ms > self._timeout_ms:
            self._start_ns = None
            self.child.reset()
            self.last_status = Status.FAILURE
            return Status.FAILURE

        status = self.child.tick(bb)
        self.motor_command = self.child.motor_command
        if status != Status.RUNNING:
            self._start_ns = None
        self.last_status = status
        return status

    def reset(self) -> None:
        super().reset()
        self._start_ns = None
        self.child.reset()


class Cooldown(Node):
    """Decorator: after child succeeds, blocks for cooldown_ms.

    Prevents thrashing by enforcing a minimum time between consecutive
    executions. Returns FAILURE during cooldown period.
    """

    def __init__(self, name: str, child: Node, cooldown_ms: float) -> None:
        super().__init__(name)
        self.child = child
        self._cooldown_ms = cooldown_ms
        self._last_success_ns: int | None = None

    def tick(self, bb: Blackboard) -> Status:
        if self._last_success_ns is not None:
            elapsed = (time.monotonic_ns() - self._last_success_ns) / 1_000_000.0
            if elapsed < self._cooldown_ms:
                self.last_status = Status.FAILURE
                return Status.FAILURE

        status = self.child.tick(bb)
        self.motor_command = self.child.motor_command
        if status == Status.SUCCESS:
            self._last_success_ns = time.monotonic_ns()
        self.last_status = status
        return status

    def reset(self) -> None:
        super().reset()
        self._last_success_ns = None
        self.child.reset()


class ConfidenceGate(Node):
    """Decorator: requires child to succeed N consecutive times.

    Filters VLM single-frame noise. Child must return SUCCESS on
    `required_frames` consecutive ticks before this node returns SUCCESS.
    Any FAILURE resets the streak counter.
    """

    def __init__(self, name: str, child: Node, required_frames: int = 2) -> None:
        super().__init__(name)
        self.child = child
        self._required = required_frames
        self._streak = 0

    def tick(self, bb: Blackboard) -> Status:
        status = self.child.tick(bb)
        self.motor_command = self.child.motor_command
        if status == Status.SUCCESS:
            self._streak += 1
            if self._streak >= self._required:
                self._streak = 0
                self.last_status = Status.SUCCESS
                return Status.SUCCESS
            self.last_status = Status.RUNNING
            return Status.RUNNING
        self._streak = 0
        self.last_status = status
        return status

    def reset(self) -> None:
        super().reset()
        self._streak = 0
        self.child.reset()


class ReactiveSelector(Node):
    """Selector that re-evaluates from the first child on every tick.

    Unlike standard Selector (which resumes from the last RUNNING child),
    ReactiveSelector always starts from child[0]. This ensures high-
    priority conditions (emergency check) always get evaluated first,
    even if a lower-priority child was RUNNING.
    """

    def __init__(self, name: str, children: list[Node]) -> None:
        super().__init__(name)
        self.children = children
        self._running_child: int | None = None

    def tick(self, bb: Blackboard) -> Status:
        for i, child in enumerate(self.children):
            status = child.tick(bb)
            if child.motor_command is not None:
                self.motor_command = child.motor_command
            if status == Status.SUCCESS:
                if self._running_child is not None and self._running_child != i:
                    self.children[self._running_child].reset()
                self._running_child = None
                self.last_status = Status.SUCCESS
                return Status.SUCCESS
            if status == Status.RUNNING:
                if self._running_child is not None and self._running_child != i:
                    self.children[self._running_child].reset()
                self._running_child = i
                self.last_status = Status.RUNNING
                return Status.RUNNING
        self._running_child = None
        self.last_status = Status.FAILURE
        return Status.FAILURE

    def reset(self) -> None:
        super().reset()
        self._running_child = None
        for child in self.children:
            child.reset()
