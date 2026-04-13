"""IntentExecutor — lightweight rule engine mapping (intent + spatial state) to MotorCommand.

NOT an LLM. This is a hard-coded decision matrix for v1 Minecraft adapter.
Decision matrix maps (IntentType, facing) to the appropriate motor action.
"""

from __future__ import annotations

from gamemind.bt.motor_command import MotorCommand
from gamemind.intent.models import Intent, IntentType

# Canonical facing values from SpatialState
_FACING_DOWN = "looking_down"
_FACING_HORIZON = "looking_at_horizon"
_FACING_UP = "looking_up"


class IntentExecutor:
    """Map (intent + spatial snapshot) -> MotorCommand.

    adapter_actions: dict of valid action names (e.g. {"forward": "w", ...}).
    Used for validation — if an action isn't in the adapter, we fall back to idle.
    """

    def __init__(self, adapter_actions: dict[str, str]) -> None:
        self._actions = adapter_actions
        self._orient_step = 0  # tracks multi-step orient sequences

    def next_action(
        self,
        intent: Intent,
        spatial_snapshot: str,
        crosshair_block: str | None,
        facing: str | None,
        anchor_direction: str | None = None,
    ) -> MotorCommand:
        """Given intent + spatial state, return next motor command."""
        intent_type = intent.intent_type

        if intent_type == IntentType.APPROACH:
            return self._approach(facing, anchor_direction)
        elif intent_type == IntentType.ATTACK_TARGET:
            return self._attack(facing, crosshair_block, intent.target_anchor, anchor_direction)
        elif intent_type == IntentType.LOOK_AROUND:
            return self._look_around(facing)
        elif intent_type == IntentType.RETREAT:
            return self._retreat(facing)
        else:
            raise ValueError(f"Unknown intent type: {intent_type}")

    def reset(self) -> None:
        self._orient_step = 0

    # -- Private dispatch per intent type --

    def _approach(self, facing: str | None, anchor_direction: str | None) -> MotorCommand:
        if facing == _FACING_DOWN:
            return self._safe_tap("look_up")
        if facing == _FACING_UP:
            return self._safe_tap("look_down")
        # facing == horizon (or None, treat as horizon)
        if anchor_direction == "ahead":
            return self._safe_hold("forward")
        return self._orient_toward(anchor_direction)

    def _attack(
        self,
        facing: str | None,
        crosshair_block: str | None,
        target_anchor: str | None,
        anchor_direction: str | None,
    ) -> MotorCommand:
        if facing == _FACING_DOWN:
            return self._safe_tap("look_up")
        if facing == _FACING_UP:
            return self._safe_tap("look_down")
        # facing == horizon
        if target_anchor and crosshair_block and crosshair_block == target_anchor:
            return self._safe_hold("attack")
        return self._orient_toward(anchor_direction)

    def _look_around(self, facing: str | None) -> MotorCommand:
        if facing == _FACING_DOWN:
            self._orient_step = 0  # reset scan on facing correction
            return self._safe_tap("look_up")
        if facing == _FACING_UP:
            self._orient_step = 0
            return self._safe_tap("look_down")
        # facing == horizon — multi-step scan: left, left, right, right, right, right
        # This covers ~360 degrees (2 lefts = 180°, then 4 rights = 360° back + 180° further)
        scan_sequence = [
            "turn_left", "turn_left",           # scan left 180°
            "turn_right", "turn_right",         # return to center
            "turn_right", "turn_right",         # scan right 180°
        ]
        if self._orient_step < len(scan_sequence):
            action = scan_sequence[self._orient_step]
            self._orient_step += 1
            return self._safe_tap(action)
        # Full scan done — cycle back
        self._orient_step = 0
        return self._safe_tap("turn_left")

    def _retreat(self, facing: str | None) -> MotorCommand:
        if facing == _FACING_DOWN:
            return self._safe_tap("look_up")
        if facing == _FACING_UP:
            return self._safe_tap("look_down")
        # facing == horizon
        return self._safe_hold("backward")

    # -- Orientation logic --

    def _orient_toward(self, anchor_direction: str | None) -> MotorCommand:
        """Emit turn commands to face the anchor direction."""
        if anchor_direction is None:
            # No direction info — scan by turning left
            return self._safe_tap("turn_left")

        # Direction mapping: which way to turn
        left_dirs = {"left", "ahead_left"}
        right_dirs = {"right", "ahead_right"}
        behind_dirs = {"behind"}

        if anchor_direction in left_dirs:
            return self._safe_tap("turn_left")
        if anchor_direction in right_dirs:
            return self._safe_tap("turn_right")
        if anchor_direction in behind_dirs:
            # Turn around — always turn left for consistency
            return self._safe_tap("turn_left")
        # "ahead" or unknown — just go forward
        return self._safe_hold("forward")

    # -- Safe action helpers (validate against adapter) --

    def _safe_tap(self, action: str) -> MotorCommand:
        if action in self._actions:
            return MotorCommand.tap(action)
        return MotorCommand.idle()

    def _safe_hold(self, action: str) -> MotorCommand:
        if action in self._actions:
            return MotorCommand.hold(action)
        return MotorCommand.idle()
