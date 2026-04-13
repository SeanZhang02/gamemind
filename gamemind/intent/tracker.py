"""IntentTracker — monitors intent execution progress.

Replaces StuckDetector + camera guard + subgoal advancement from the
old architecture. Determines if an intent is PROGRESSING, COMPLETED,
STALLED, or BLOCKED based on spatial perception changes over time.
"""

from __future__ import annotations

from gamemind.intent.models import Intent, IntentStatus, IntentType

# Default stall thresholds per intent type (frames at ~2Hz perception rate)
_DEFAULT_STALL_THRESHOLDS: dict[IntentType, int] = {
    IntentType.APPROACH: 10,        # 5 seconds
    IntentType.LOOK_AROUND: 10,     # 5 seconds
    IntentType.ATTACK_TARGET: 16,   # 8 seconds (attacking takes time)
    IntentType.RETREAT: 6,          # 3 seconds (fast)
}

# Distance ordering for progress detection
_DISTANCE_ORDER: dict[str, int] = {
    "far": 0,
    "medium": 1,
    "close": 2,
}

# Direction ordering toward "ahead" for progress detection
_DIRECTION_TOWARD_AHEAD: dict[str, int] = {
    "behind": 0,
    "left": 1,
    "right": 1,
    "ahead_left": 2,
    "ahead_right": 2,
    "ahead": 3,
}

# How many consecutive crosshair-match frames needed for attack completion
_ATTACK_MATCH_THRESHOLD = 3

# How many distinct direction categories for look_around completion
_LOOK_AROUND_DIRECTION_THRESHOLD = 3

# How many backward frames needed for retreat completion
_RETREAT_FRAME_THRESHOLD = 5


class IntentTracker:
    """Track execution progress of the current intent.

    Call start() when a new intent is issued, then check_progress()
    on every perception tick. Read status to get the current assessment.
    """

    def __init__(self) -> None:
        self._intent: Intent | None = None
        self._status = IntentStatus.IDLE
        self._no_change_frames = 0
        self._total_frames = 0
        self._prev_distance: str | None = None
        self._prev_direction: str | None = None
        self._observed_directions: set[str] = set()  # for look_around completion
        self._consecutive_crosshair_matches = 0      # for attack completion
        self._backward_frames = 0                     # for retreat completion

    def start(self, intent: Intent) -> None:
        """Begin tracking a new intent. Resets all internal state."""
        self._intent = intent
        self._status = IntentStatus.PROGRESSING
        self._no_change_frames = 0
        self._total_frames = 0
        self._observed_directions = set()
        self._prev_distance = None
        self._prev_direction = None
        self._consecutive_crosshair_matches = 0
        self._backward_frames = 0

    def check_progress(
        self,
        *,
        crosshair_block: str | None,
        target_anchor_direction: str | None,
        target_anchor_distance: str | None,
        facing: str | None,
        health: float | None = None,
    ) -> IntentStatus:
        """Check if current intent is progressing. Call each perception tick.

        Returns the updated IntentStatus.
        """
        if self._intent is None:
            return IntentStatus.IDLE

        self._total_frames += 1

        # Health-based BLOCKED check (applies to ALL intent types)
        if health is not None and health < 0.5:
            self._status = IntentStatus.BLOCKED
            return self._status

        # Auto-stall on max_steps
        if self._total_frames >= self._intent.max_steps:
            self._status = IntentStatus.STALLED
            return self._status

        # Intent-specific progress detection
        intent_type = self._intent.intent_type

        if intent_type == IntentType.APPROACH:
            return self._check_approach(target_anchor_direction, target_anchor_distance)
        elif intent_type == IntentType.ATTACK_TARGET:
            return self._check_attack(crosshair_block)
        elif intent_type == IntentType.LOOK_AROUND:
            return self._check_look_around(target_anchor_direction)
        elif intent_type == IntentType.RETREAT:
            return self._check_retreat()

        return self._status

    @property
    def status(self) -> IntentStatus:
        return self._status

    @property
    def active_intent(self) -> Intent | None:
        return self._intent

    def reset(self) -> None:
        """Explicitly reset to idle. Clears intent and all tracking state."""
        self._intent = None
        self._status = IntentStatus.IDLE
        self._no_change_frames = 0
        self._total_frames = 0
        self._observed_directions = set()
        self._prev_distance = None
        self._prev_direction = None
        self._consecutive_crosshair_matches = 0
        self._backward_frames = 0

    # -- Intent-specific progress checks --

    def _check_approach(
        self,
        direction: str | None,
        distance: str | None,
    ) -> IntentStatus:
        """APPROACH progress detection.

        COMPLETED: distance == "close" AND direction == "ahead"
        PROGRESSING: distance decreased OR direction moved toward ahead
        STALLED: stall_threshold frames with no distance/direction change
        BLOCKED: distance stuck at "close" but direction not "ahead" (obstacle),
                 OR health dropped below 0.5 during intent execution
        """
        # Check completion
        if distance == "close" and direction == "ahead":
            self._status = IntentStatus.COMPLETED
            return self._status

        # Check obstacle-based BLOCKED: stuck close but can't face target
        if (
            distance == "close"
            and direction is not None
            and direction != "ahead"
            and self._prev_distance == "close"
            and self._no_change_frames >= 5  # stuck for 5 frames at close range
        ):
            self._status = IntentStatus.BLOCKED
            return self._status

        # Check for progress (any change toward target)
        made_progress = False

        if distance is not None and self._prev_distance is not None:
            curr_rank = _DISTANCE_ORDER.get(distance, -1)
            prev_rank = _DISTANCE_ORDER.get(self._prev_distance, -1)
            if curr_rank > prev_rank:
                made_progress = True

        if direction is not None and self._prev_direction is not None:
            curr_rank = _DIRECTION_TOWARD_AHEAD.get(direction, -1)
            prev_rank = _DIRECTION_TOWARD_AHEAD.get(self._prev_direction, -1)
            if curr_rank > prev_rank:
                made_progress = True

        # Update previous values
        if distance is not None:
            self._prev_distance = distance
        if direction is not None:
            self._prev_direction = direction

        if made_progress:
            self._no_change_frames = 0
            self._status = IntentStatus.PROGRESSING
        else:
            self._no_change_frames += 1
            threshold = _DEFAULT_STALL_THRESHOLDS.get(
                IntentType.APPROACH, 10
            )
            if self._no_change_frames >= threshold:
                self._status = IntentStatus.STALLED

        return self._status

    def _check_attack(
        self,
        crosshair_block: str | None,
    ) -> IntentStatus:
        """ATTACK_TARGET progress detection.

        COMPLETED: crosshair matches target for 3+ consecutive frames
        PROGRESSING: crosshair matches target
        STALLED: target not at crosshair for stall_threshold frames
        """
        target = self._intent.target_anchor if self._intent else None

        if target and crosshair_block and crosshair_block == target:
            self._consecutive_crosshair_matches += 1
            self._no_change_frames = 0

            if self._consecutive_crosshair_matches >= _ATTACK_MATCH_THRESHOLD:
                self._status = IntentStatus.COMPLETED
            else:
                self._status = IntentStatus.PROGRESSING
        else:
            self._consecutive_crosshair_matches = 0
            self._no_change_frames += 1
            threshold = _DEFAULT_STALL_THRESHOLDS.get(
                IntentType.ATTACK_TARGET, 16
            )
            if self._no_change_frames >= threshold:
                self._status = IntentStatus.STALLED

        return self._status

    def _check_look_around(
        self,
        anchor_direction: str | None,
    ) -> IntentStatus:
        """LOOK_AROUND progress detection.

        COMPLETED: at least 3 distinct direction categories observed
        PROGRESSING: new anchor directions discovered
        STALLED: no new directions for stall_threshold frames
        """
        if anchor_direction is not None:
            prev_count = len(self._observed_directions)
            self._observed_directions.add(anchor_direction)
            new_count = len(self._observed_directions)

            if new_count >= _LOOK_AROUND_DIRECTION_THRESHOLD:
                self._status = IntentStatus.COMPLETED
                return self._status

            if new_count > prev_count:
                self._no_change_frames = 0
                self._status = IntentStatus.PROGRESSING
                return self._status

        # No new direction found this frame
        self._no_change_frames += 1
        threshold = _DEFAULT_STALL_THRESHOLDS.get(
            IntentType.LOOK_AROUND, 10
        )
        if self._no_change_frames >= threshold:
            self._status = IntentStatus.STALLED

        return self._status

    def _check_retreat(self) -> IntentStatus:
        """RETREAT progress detection.

        COMPLETED: 5+ frames of backward movement executed
        STALLED: stall_threshold frames with no movement
        """
        # We count total frames as backward frames since retreat
        # is a simple "keep going backward" intent.
        # The executor always emits backward when facing horizon,
        # so we count each tick as a backward frame.
        self._backward_frames += 1

        if self._backward_frames >= _RETREAT_FRAME_THRESHOLD:
            self._status = IntentStatus.COMPLETED
            return self._status

        self._status = IntentStatus.PROGRESSING
        return self._status
