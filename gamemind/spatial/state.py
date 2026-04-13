"""SpatialState — thread-safe double-buffered spatial world model.

Maintains a persistent text world model across frames. Uses the same
double-buffered swap pattern as Blackboard: producers write to the back
buffer via update(), swap() promotes back to front, and consumers read
from front via snapshot()/diff().

Anchor management: new anchors are created on first observation, refreshed
on re-observation, and pruned when their last_seen_ns exceeds anchor_max_age_ns.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class SpatialAnchor:
    """A landmark observed in the game world."""

    label: str  # e.g. "oak_tree"
    direction: str  # e.g. "ahead_right"
    distance: str  # e.g. "close"
    first_seen_ns: int  # monotonic timestamp
    last_seen_ns: int  # refreshed on re-observation
    confidence: float = 1.0


@dataclass(frozen=True)
class SpatialPerception:
    """Parsed VLM output for one frame."""

    block: str | None
    facing: str | None  # "looking_down" | "looking_at_horizon" | "looking_up"
    spatial_context: str | None  # free text description
    anchors: list[dict] | None  # raw anchor dicts from VLM
    health: float | None
    entities: list[str] | None


class _SpatialBuffer:
    """Internal mutable buffer holding one snapshot of spatial state."""

    def __init__(self) -> None:
        self.facing: str | None = None
        self.block: str | None = None
        self.spatial_context: str | None = None
        self.health: float | None = None
        self.entities: list[str] = []
        self.anchors: dict[str, SpatialAnchor] = {}
        self.last_diff: str | None = None

    def copy_from(self, other: _SpatialBuffer) -> _SpatialBuffer:
        """Copy state from other buffer (for swap continuity)."""
        self.facing = other.facing
        self.block = other.block
        self.spatial_context = other.spatial_context
        self.health = other.health
        self.entities = list(other.entities)
        self.anchors = {
            k: SpatialAnchor(
                label=v.label,
                direction=v.direction,
                distance=v.distance,
                first_seen_ns=v.first_seen_ns,
                last_seen_ns=v.last_seen_ns,
                confidence=v.confidence,
            )
            for k, v in other.anchors.items()
        }
        self.last_diff = other.last_diff
        return self


class SpatialState:
    """Thread-safe double-buffered spatial world model.

    update() writes to the back buffer (perception thread).
    swap() promotes back buffer to front (after Blackboard.swap()).
    snapshot() / diff() read from the front buffer (orchestrator thread).
    """

    VALID_FACINGS = {"looking_down", "looking_at_horizon", "looking_up"}
    VALID_DIRECTIONS = {"ahead", "ahead_left", "ahead_right", "left", "right", "behind"}
    VALID_DISTANCES = {"close", "medium", "far"}

    def __init__(self, anchor_max_age_ns: int = 10_000_000_000) -> None:  # 10s default
        self._lock = threading.Lock()
        self._back = _SpatialBuffer()
        self._front = _SpatialBuffer()
        self._anchor_max_age_ns = anchor_max_age_ns
        self._prev_perception: SpatialPerception | None = None

    def update(self, perception: SpatialPerception) -> None:
        """Write new perception to back buffer. Call from perception thread."""
        with self._lock:
            buf = self._back
            buf.facing = (
                perception.facing
                if perception.facing in self.VALID_FACINGS
                else buf.facing
            )
            buf.block = perception.block
            buf.spatial_context = perception.spatial_context
            buf.health = perception.health
            buf.entities = perception.entities or []

            # Merge anchors: refresh existing, add new, prune expired
            now_ns = time.monotonic_ns()
            if perception.anchors:
                for raw in perception.anchors:
                    label = raw.get("label", "")
                    direction = raw.get("direction", "")
                    distance = raw.get("distance", "")
                    if (
                        not label
                        or direction not in self.VALID_DIRECTIONS
                        or distance not in self.VALID_DISTANCES
                    ):
                        continue
                    existing = buf.anchors.get(label)
                    if existing:
                        existing.direction = direction
                        existing.distance = distance
                        existing.last_seen_ns = now_ns
                        existing.confidence = 1.0
                    else:
                        buf.anchors[label] = SpatialAnchor(
                            label=label,
                            direction=direction,
                            distance=distance,
                            first_seen_ns=now_ns,
                            last_seen_ns=now_ns,
                        )

            # Prune expired anchors
            expired = [
                k
                for k, v in buf.anchors.items()
                if (now_ns - v.last_seen_ns) > self._anchor_max_age_ns
            ]
            for k in expired:
                del buf.anchors[k]

            # Compute diff for next frame's VLM prompt
            buf.last_diff = self._compute_diff(self._prev_perception, perception)
            self._prev_perception = perception

    def swap(self) -> None:
        """Promote back buffer to front. Call after Blackboard.swap()."""
        with self._lock:
            self._front, self._back = self._back, self._front.copy_from(self._back)

    def get_anchor(self, label: str) -> SpatialAnchor | None:
        """Look up a specific anchor by label from front buffer.

        Case-insensitive. Returns None if not found or expired.
        Call from orchestrator thread.
        """
        with self._lock:
            label_lower = label.lower()
            for key, anchor in self._front.anchors.items():
                if key.lower() == label_lower:
                    return SpatialAnchor(
                        label=anchor.label,
                        direction=anchor.direction,
                        distance=anchor.distance,
                        first_seen_ns=anchor.first_seen_ns,
                        last_seen_ns=anchor.last_seen_ns,
                        confidence=anchor.confidence,
                    )
            return None

    def snapshot(self) -> str:
        """Return current world model as text for Brain prompt.

        Call from orchestrator thread (reads front buffer).
        """
        with self._lock:
            buf = self._front
            parts: list[str] = []
            if buf.facing:
                parts.append(f"Camera: {buf.facing.replace('_', ' ')}.")
            if buf.block:
                parts.append(f"Crosshair on: {buf.block}.")
            if buf.spatial_context:
                parts.append(f"Scene: {buf.spatial_context}")

            # Top 20 anchors sorted by recency
            sorted_anchors = sorted(
                buf.anchors.values(), key=lambda a: a.last_seen_ns, reverse=True
            )[:20]
            if sorted_anchors:
                anchor_strs = [
                    f"{a.label} ({a.direction}, {a.distance})" for a in sorted_anchors
                ]
                parts.append(f"Nearby: {', '.join(anchor_strs)}.")
            else:
                parts.append("No notable objects visible.")

            if buf.entities:
                parts.append(f"Entities: {', '.join(buf.entities)}.")
            if buf.health is not None:
                parts.append(f"Health: {buf.health:.1f}.")

            return " ".join(parts)

    def diff(self) -> str | None:
        """Return last computed diff text (for VLM prompt injection)."""
        with self._lock:
            return self._front.last_diff

    def _compute_diff(
        self, prev: SpatialPerception | None, curr: SpatialPerception
    ) -> str | None:
        """Compute textual diff between two consecutive perceptions."""
        if prev is None:
            return None
        parts: list[str] = []
        if prev.facing != curr.facing and curr.facing:
            parts.append(f"Camera changed to {curr.facing.replace('_', ' ')}")
        if prev.block != curr.block:
            parts.append(
                f"Block changed from {prev.block or 'none'} to {curr.block or 'none'}"
            )
        return ". ".join(parts) if parts else None

    def clear(self) -> None:
        """Reset all state."""
        with self._lock:
            self._back = _SpatialBuffer()
            self._front = _SpatialBuffer()
            self._prev_perception = None
