"""Tests for IntentTracker — progress detection for all intent types.

18 tests covering completion, progression, stalling, max_steps, resets,
and intent switching for APPROACH, ATTACK_TARGET, LOOK_AROUND, RETREAT.
"""

from __future__ import annotations

from gamemind.intent.models import Intent, IntentStatus, IntentType
from gamemind.intent.tracker import IntentTracker


def _approach(target: str = "oak_tree", max_steps: int = 20) -> Intent:
    return Intent(IntentType.APPROACH, target_anchor=target, max_steps=max_steps)


def _attack(target: str = "zombie", max_steps: int = 20) -> Intent:
    return Intent(IntentType.ATTACK_TARGET, target_anchor=target, max_steps=max_steps)


def _look_around(max_steps: int = 20) -> Intent:
    return Intent(IntentType.LOOK_AROUND, max_steps=max_steps)


def _retreat(max_steps: int = 20) -> Intent:
    return Intent(IntentType.RETREAT, max_steps=max_steps)


def _tick(
    tracker: IntentTracker,
    *,
    crosshair: str | None = None,
    direction: str | None = None,
    distance: str | None = None,
    facing: str | None = "looking_at_horizon",
) -> IntentStatus:
    """Convenience wrapper for check_progress with keyword args."""
    return tracker.check_progress(
        crosshair_block=crosshair,
        target_anchor_direction=direction,
        target_anchor_distance=distance,
        facing=facing,
    )


# ── Basic lifecycle ─────────────────────────────────────────────


class TestLifecycle:
    def test_start_sets_progressing(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        assert t.status == IntentStatus.PROGRESSING

    def test_idle_when_no_intent(self) -> None:
        t = IntentTracker()
        result = _tick(t)
        assert result == IntentStatus.IDLE

    def test_reset_to_idle(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        assert t.status == IntentStatus.PROGRESSING
        t.reset()
        assert t.status == IntentStatus.IDLE
        assert t.active_intent is None

    def test_switch_intent_resets(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        # Simulate some progress
        _tick(t, direction="left", distance="far")
        _tick(t, direction="ahead_left", distance="medium")
        # Switch to attack — should reset
        t.start(_attack())
        assert t.status == IntentStatus.PROGRESSING
        assert t.active_intent is not None
        assert t.active_intent.intent_type == IntentType.ATTACK_TARGET

    def test_max_steps_auto_stall(self) -> None:
        t = IntentTracker()
        t.start(_approach(max_steps=5))
        for _ in range(4):
            result = _tick(t, direction="left", distance="far")
        # Frame 4 should not stall yet
        assert result != IntentStatus.STALLED  # type: ignore[possibly-undefined]
        # Frame 5 → auto-stall
        result = _tick(t, direction="left", distance="far")
        assert result == IntentStatus.STALLED


# ── APPROACH tests ──────────────────────────────────────────────


class TestApproach:
    def test_approach_completed(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        result = _tick(t, direction="ahead", distance="close")
        assert result == IntentStatus.COMPLETED

    def test_approach_progressing_distance_decrease(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        _tick(t, direction="ahead", distance="far")
        result = _tick(t, direction="ahead", distance="medium")
        assert result == IntentStatus.PROGRESSING

    def test_approach_progressing_direction_toward(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        _tick(t, direction="left", distance="medium")
        result = _tick(t, direction="ahead_left", distance="medium")
        assert result == IntentStatus.PROGRESSING

    def test_approach_stalled_no_change(self) -> None:
        t = IntentTracker()
        t.start(_approach())
        # First tick sets baseline
        _tick(t, direction="left", distance="far")
        # 10 more ticks with no change → stalled (threshold is 10)
        for _ in range(10):
            result = _tick(t, direction="left", distance="far")
        assert result == IntentStatus.STALLED  # type: ignore[possibly-undefined]


# ── ATTACK_TARGET tests ────────────────────────────────────────


class TestAttack:
    def test_attack_completed_3_frames(self) -> None:
        t = IntentTracker()
        t.start(_attack("zombie"))
        _tick(t, crosshair="zombie")
        _tick(t, crosshair="zombie")
        result = _tick(t, crosshair="zombie")
        assert result == IntentStatus.COMPLETED

    def test_attack_progressing_crosshair_matches(self) -> None:
        t = IntentTracker()
        t.start(_attack("zombie"))
        result = _tick(t, crosshair="zombie")
        assert result == IntentStatus.PROGRESSING

    def test_attack_stalled_no_match(self) -> None:
        t = IntentTracker()
        t.start(_attack("zombie"))
        for _ in range(16):
            result = _tick(t, crosshair="dirt")
        assert result == IntentStatus.STALLED  # type: ignore[possibly-undefined]


# ── LOOK_AROUND tests ──────────────────────────────────────────


class TestLookAround:
    def test_look_around_completed_3_directions(self) -> None:
        t = IntentTracker()
        t.start(_look_around())
        _tick(t, direction="ahead")
        _tick(t, direction="left")
        result = _tick(t, direction="right")
        assert result == IntentStatus.COMPLETED

    def test_look_around_progressing_new_direction(self) -> None:
        t = IntentTracker()
        t.start(_look_around())
        result = _tick(t, direction="ahead")
        assert result == IntentStatus.PROGRESSING

    def test_look_around_stalled(self) -> None:
        t = IntentTracker()
        t.start(_look_around())
        _tick(t, direction="ahead")  # first direction
        # 10 ticks with same direction → stalled
        for _ in range(10):
            result = _tick(t, direction="ahead")
        assert result == IntentStatus.STALLED  # type: ignore[possibly-undefined]


# ── RETREAT tests ───────────────────────────────────────────────


class TestRetreat:
    def test_retreat_completed_5_frames(self) -> None:
        t = IntentTracker()
        t.start(_retreat())
        for _ in range(4):
            result = _tick(t)
        assert result == IntentStatus.PROGRESSING  # type: ignore[possibly-undefined]
        result = _tick(t)
        assert result == IntentStatus.COMPLETED

    def test_retreat_stalled(self) -> None:
        """Retreat with max_steps lower than completion threshold → stalled."""
        t = IntentTracker()
        t.start(_retreat(max_steps=3))
        for _ in range(3):
            result = _tick(t)
        # max_steps=3, so frame 3 triggers auto-stall before reaching 5 backward frames
        assert result == IntentStatus.STALLED  # type: ignore[possibly-undefined]


# ── Cross-cutting behavior ──────────────────────────────────────


class TestCrossCutting:
    def test_stalled_to_progressing_on_change(self) -> None:
        """If progress resumes after stalling, status returns to PROGRESSING."""
        t = IntentTracker()
        t.start(_approach())
        # Set baseline
        _tick(t, direction="left", distance="far")
        # Stall out
        for _ in range(10):
            _tick(t, direction="left", distance="far")
        assert t.status == IntentStatus.STALLED
        # Now make progress — distance decreases
        result = _tick(t, direction="left", distance="medium")
        assert result == IntentStatus.PROGRESSING
