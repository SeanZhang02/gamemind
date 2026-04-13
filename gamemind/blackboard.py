"""Blackboard — shared world state between all cognitive components.

Thread-safe double-buffered shared state with per-slot exponential
confidence decay. All producers (VLM, Watchdog, Planner, Action) write
to the back buffer; consumers (FSM, BT, Motor) read from the front
buffer via atomic swap.

14 named slots grouped by producer:
  VLM (half_life 600ms): crosshair_block, entities_nearby, ui_state,
      player_facing, health, hunger
  Watchdog (half_life 200ms): frame_diff_score, vlm_last_update_ns
  Planner (half_life 8000ms): current_subgoal, plan_sequence, abort_override
  Action (half_life 10000ms): last_action, action_streak

Confidence = initial × 0.5^(age_ms / half_life_ms).
Frame-consistency bonus: 3 consecutive identical values → ×1.15 (cap 0.95).
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Producer(str, Enum):
    VLM = "vlm"
    WATCHDOG = "watchdog"
    PLANNER = "planner"
    ACTION = "action"


@dataclass
class BBEntry:
    value: Any
    ts_ns: int
    producer: Producer
    initial_confidence: float
    half_life_ms: float
    seq: int = 0

    def confidence_at(self, now_ns: int) -> float:
        age_ms = (now_ns - self.ts_ns) / 1_000_000.0
        if age_ms <= 0:
            return self.initial_confidence
        return self.initial_confidence * math.pow(0.5, age_ms / self.half_life_ms)

    def is_expired(self, now_ns: int, expiry_ms: float) -> bool:
        age_ms = (now_ns - self.ts_ns) / 1_000_000.0
        return age_ms > expiry_ms


SLOT_CONFIG: dict[str, tuple[Producer, float, float, float]] = {
    "crosshair_block": (Producer.VLM, 600.0, 1200.0, 0.75),
    "entities_nearby": (Producer.VLM, 600.0, 2000.0, 0.75),
    "ui_state": (Producer.VLM, 600.0, 1500.0, 0.75),
    "player_facing": (Producer.VLM, 600.0, 2000.0, 0.75),
    "health": (Producer.VLM, 600.0, 5000.0, 0.75),
    "hunger": (Producer.VLM, 600.0, 5000.0, 0.75),
    "frame_diff_score": (Producer.WATCHDOG, 200.0, 500.0, 0.9),
    "vlm_last_update_ns": (Producer.VLM, 200.0, 60000.0, 1.0),
    "current_subgoal": (Producer.PLANNER, 8000.0, 30000.0, 0.95),
    "plan_sequence": (Producer.PLANNER, 8000.0, 30000.0, 0.95),
    "abort_override": (Producer.PLANNER, 8000.0, 15000.0, 0.95),
    "last_action": (Producer.ACTION, 10000.0, 10000.0, 0.8),
    "action_streak": (Producer.ACTION, 10000.0, 10000.0, 0.8),
    "vlm_suggested_action": (Producer.VLM, 600.0, 1200.0, 0.75),
}

# Type contracts for key slots (documentation, not runtime enforcement):
#   current_subgoal: str — e.g. "find_tree", "approach_tree", "chop_trunk"
#   plan_sequence: list[str] — ordered subgoal names
#   crosshair_block: str — Minecraft block ID e.g. "oak_log", "air", "stone"
#   health: float 0.0-1.0
#   entities_nearby: list[str] — e.g. ["zombie", "cow"]
#   last_action: str — adapter action name e.g. "forward", "attack"
#   action_streak: int — consecutive identical action count

_CONSISTENCY_WINDOW = 5
_CONSISTENCY_BONUS_3 = 1.15
_CONSISTENCY_BONUS_2 = 1.05
_CONSISTENCY_CHANGE_PENALTY = 0.85
_CONFIDENCE_CAP = 0.95


@dataclass
class ReadResult:
    value: Any
    confidence: float
    age_ms: float
    producer: Producer
    expired: bool


class Blackboard:
    """Thread-safe double-buffered blackboard.

    write() writes to the back buffer. swap() atomically promotes the
    back buffer to front. read() always reads from front buffer.
    This ensures Watchdog (reading front) never sees half-updated VLM data.
    """

    def __init__(self) -> None:
        self._front: dict[str, BBEntry] = {}
        self._back: dict[str, BBEntry] = {}
        self._lock = threading.Lock()
        self._seq = 0
        self._history: dict[str, list[Any]] = {k: [] for k in SLOT_CONFIG}

    def write(self, key: str, value: Any, producer: Producer | None = None) -> None:
        if key not in SLOT_CONFIG:
            return
        config_producer, half_life, _expiry, base_conf = SLOT_CONFIG[key]
        if producer is not None and producer != config_producer:
            return

        now_ns = time.monotonic_ns()
        with self._lock:
            confidence = self._compute_confidence(key, value, base_conf)
            self._seq += 1
            self._back[key] = BBEntry(
                value=value,
                ts_ns=now_ns,
                producer=config_producer,
                initial_confidence=confidence,
                half_life_ms=half_life,
                seq=self._seq,
            )

    def swap(self) -> None:
        with self._lock:
            for key, entry in self._back.items():
                self._front[key] = entry
            self._back.clear()

    def read(self, key: str) -> ReadResult | None:
        with self._lock:
            entry = self._front.get(key)
            if entry is None:
                return None
            value = entry.value
            ts_ns = entry.ts_ns
            producer = entry.producer
            initial_conf = entry.initial_confidence
            half_life = entry.half_life_ms
        now_ns = time.monotonic_ns()
        _, _, expiry, _ = SLOT_CONFIG[key]
        age_ms = (now_ns - ts_ns) / 1_000_000.0
        expired = age_ms > expiry
        if expired:
            conf = 0.0
            value = None
        else:
            conf = initial_conf * math.pow(0.5, age_ms / half_life)
        return ReadResult(
            value=value,
            confidence=conf,
            age_ms=age_ms,
            producer=producer,
            expired=expired,
        )

    def read_value(self, key: str, min_confidence: float = 0.0) -> Any:
        result = self.read(key)
        if result is None or result.expired or result.confidence < min_confidence:
            return None
        return result.value

    def snapshot(self) -> dict[str, ReadResult]:
        now_ns = time.monotonic_ns()
        with self._lock:
            snap = {}
            for key, entry in self._front.items():
                _, _, expiry, _ = SLOT_CONFIG[key]
                expired = entry.is_expired(now_ns, expiry)
                conf = entry.confidence_at(now_ns) if not expired else 0.0
                snap[key] = ReadResult(
                    value=entry.value if not expired else None,
                    confidence=conf,
                    age_ms=(now_ns - entry.ts_ns) / 1_000_000.0,
                    producer=entry.producer,
                    expired=expired,
                )
            return snap

    def clear(self) -> None:
        with self._lock:
            self._front.clear()
            self._back.clear()
            self._history = {k: [] for k in SLOT_CONFIG}
            self._seq = 0

    def _compute_confidence(self, key: str, value: Any, base: float) -> float:
        history = self._history.get(key, [])
        history.append(value)
        if len(history) > _CONSISTENCY_WINDOW:
            history = history[-_CONSISTENCY_WINDOW:]
        self._history[key] = history

        if len(history) < 2:
            return min(base, _CONFIDENCE_CAP)

        if len(history) >= 3 and history[-1] == history[-2] == history[-3]:
            return min(base * _CONSISTENCY_BONUS_3, _CONFIDENCE_CAP)
        if history[-1] == history[-2]:
            return min(base * _CONSISTENCY_BONUS_2, _CONFIDENCE_CAP)
        return min(base * _CONSISTENCY_CHANGE_PENALTY, _CONFIDENCE_CAP)
