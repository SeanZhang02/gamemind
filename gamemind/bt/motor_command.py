"""MotorCommand — abstract action intent from BT to Motor Thread.

BT leaf nodes produce MotorCommands. Motor Thread (Step 7) consumes
them. This Protocol decouples BT from the real InputBackend.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class MotorCommandType(Enum):
    HOLD = auto()
    RELEASE = auto()
    TAP = auto()
    IDLE = auto()


@dataclass(frozen=True)
class MotorCommand:
    """Abstract motor intent.

    action_name: adapter action key (e.g. "forward", "attack")
    command_type: HOLD (press and keep), RELEASE, TAP (press+release), IDLE (stop all)
    duration_ms: for TAP/HOLD, how long before auto-release (0 = indefinite hold)
    """

    action_name: str
    command_type: MotorCommandType
    duration_ms: float = 0.0

    @staticmethod
    def hold(action: str, duration_ms: float = 0.0) -> MotorCommand:
        return MotorCommand(action, MotorCommandType.HOLD, duration_ms)

    @staticmethod
    def release(action: str) -> MotorCommand:
        return MotorCommand(action, MotorCommandType.RELEASE)

    @staticmethod
    def tap(action: str) -> MotorCommand:
        return MotorCommand(action, MotorCommandType.TAP)

    @staticmethod
    def idle() -> MotorCommand:
        return MotorCommand("", MotorCommandType.IDLE)
