"""Motor Thread — continuous action execution with safety overrides.

Executes MotorCommands from BT, with priority chain:
  watchdog.freeze > watchdog.emergency > BT command > idle

Features:
  - Staleness timeout: if no new command for 800ms → auto idle
  - Hysteresis recovery: after idle, need 2 consecutive valid ticks
    to resume (prevents VLM jitter → walk-stop-walk-stop)
  - All commands go through adapter action→key mapping
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from gamemind.bt.motor_command import MotorCommand, MotorCommandType


@dataclass
class MotorState:
    current_action: str = ""
    is_holding: bool = False
    last_command_ns: int = 0
    is_idle: bool = True
    recovery_streak: int = 0


_STALENESS_TIMEOUT_MS = 1500.0  # must exceed VLM tick interval (1Hz=1000ms) + inference latency
_RECOVERY_THRESHOLD = 2


class Motor:
    """Motor controller with staleness timeout and hysteresis.

    Does NOT directly call InputBackend — produces resolved commands
    that the runner feeds to InputBackend. This keeps Motor testable
    without real Win32 SendInput.
    """

    def __init__(self, action_to_key: dict[str, str]) -> None:
        self._action_to_key = action_to_key
        self._state = MotorState(recovery_streak=_RECOVERY_THRESHOLD)
        self._frozen = False
        self._emergency_command: MotorCommand | None = None

    @property
    def state(self) -> MotorState:
        return self._state

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        self._frozen = True
        self._state.is_idle = True
        self._state.is_holding = False
        self._state.current_action = ""

    def unfreeze(self) -> None:
        self._frozen = False
        self._state.recovery_streak = 0

    def set_emergency(self, command: MotorCommand) -> None:
        self._emergency_command = command

    def clear_emergency(self) -> None:
        self._emergency_command = None

    def resolve(self, bt_command: MotorCommand | None) -> ResolvedCommand | None:
        """Resolve a BT motor command through the priority chain.

        Returns a ResolvedCommand with the key to press/release, or None
        if no action should be taken (frozen, stale idle, etc).
        """
        now_ns = time.monotonic_ns()

        if self._frozen:
            return ResolvedCommand(
                action="", key="", command_type=MotorCommandType.IDLE, reason="frozen"
            )

        if self._emergency_command is not None:
            key = self._action_to_key.get(self._emergency_command.action_name, "")
            return ResolvedCommand(
                action=self._emergency_command.action_name,
                key=key,
                command_type=self._emergency_command.command_type,
                reason="emergency",
                duration_ms=self._emergency_command.duration_ms,
            )

        if bt_command is None or bt_command.command_type == MotorCommandType.IDLE:
            self._check_staleness(now_ns)
            return None

        if self._state.is_idle:
            self._state.recovery_streak += 1
            if self._state.recovery_streak < _RECOVERY_THRESHOLD:
                return None
            self._state.is_idle = False
            self._state.recovery_streak = 0

        self._state.last_command_ns = now_ns
        key = self._action_to_key.get(bt_command.action_name, "")
        if not key:
            return None

        self._state.current_action = bt_command.action_name
        self._state.is_holding = bt_command.command_type == MotorCommandType.HOLD

        return ResolvedCommand(
            action=bt_command.action_name,
            key=key,
            command_type=bt_command.command_type,
            reason="bt",
            duration_ms=bt_command.duration_ms,
        )

    def _check_staleness(self, now_ns: int) -> None:
        if self._state.last_command_ns == 0:
            return
        age_ms = (now_ns - self._state.last_command_ns) / 1_000_000.0
        if age_ms > _STALENESS_TIMEOUT_MS and not self._state.is_idle:
            self._state.is_idle = True
            self._state.is_holding = False
            self._state.current_action = ""
            self._state.recovery_streak = 0

    def reset(self) -> None:
        self._state = MotorState()
        self._frozen = False
        self._emergency_command = None


@dataclass(frozen=True)
class ResolvedCommand:
    """Output of Motor.resolve() — ready for InputBackend."""

    action: str
    key: str
    command_type: MotorCommandType
    reason: str
    duration_ms: float = 0.0
