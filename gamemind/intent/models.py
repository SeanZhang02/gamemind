"""Intent data models — what the agent wants to achieve in the game world."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IntentType(str, Enum):
    APPROACH = "approach"
    LOOK_AROUND = "look_around"
    ATTACK_TARGET = "attack_target"
    RETREAT = "retreat"


class IntentStatus(str, Enum):
    IDLE = "idle"
    PROGRESSING = "progressing"
    COMPLETED = "completed"
    STALLED = "stalled"
    BLOCKED = "blocked"


@dataclass
class Intent:
    """A high-level goal the agent is currently pursuing.

    intent_type: what kind of action
    target_anchor: label from VLM anchor (e.g. "oak_tree")
    expected_outcome: human-readable description of success
    max_steps: auto-STALLED after this many frames
    reason: why this intent was chosen
    """

    intent_type: IntentType
    target_anchor: str | None = None
    expected_outcome: str = ""
    max_steps: int = 20
    reason: str = ""
