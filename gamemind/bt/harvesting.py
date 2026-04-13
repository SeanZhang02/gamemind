"""HARVESTING behavior tree — chop/mine/farm resource collection.

Game-agnostic: reads adapter target block types from Blackboard,
not hardcoded Minecraft block IDs. Follows Design Rule 2.

Tree structure (ReactiveSelector root):
  1. Emergency check (health critical → signal FSM RECOVERING)
  2. Pickup nearby drops (items on ground → walk + collect)
  3. Harvest aligned target (crosshair on target → hold attack)
  4. Align to target (target visible but off-center → adjust aim)
  5. Signal FSM navigate (no target visible → need to move)
"""

from __future__ import annotations

from gamemind.blackboard import Blackboard
from gamemind.bt.decorators import ConfidenceGate, Cooldown, ReactiveSelector, Timeout
from gamemind.bt.engine import Action, Condition, ForceSuccess, Selector, Sequence, Status
from gamemind.bt.motor_command import MotorCommand


def _health_critical(bb: Blackboard) -> bool:
    health = bb.read_value("health", min_confidence=0.3)
    if health is None:
        return False
    try:
        return float(health) < 0.3
    except (ValueError, TypeError):
        return False


def _crosshair_on_target(bb: Blackboard) -> bool:
    block = bb.read_value("crosshair_block", min_confidence=0.4)
    if block is None or block in ("air", "water", "lava", ""):
        return False
    vlm_action = bb.read_value("vlm_suggested_action")
    if vlm_action == "attack":
        return True
    subgoal_ok = bb.read_value("subgoal_ok")
    if subgoal_ok is True:
        return True
    return vlm_action not in ("forward", "turn_right", "turn_left", None)


def _drops_nearby(bb: Blackboard) -> bool:
    entities = bb.read_value("entities_nearby", min_confidence=0.3)
    if not entities or not isinstance(entities, list):
        return False
    return any("item" in str(e).lower() for e in entities)


def _no_target_in_view(bb: Blackboard) -> bool:
    block = bb.read_value("crosshair_block", min_confidence=0.3)
    return block is None or block in ("air", "water", "lava", "")


def _hold_attack(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.RUNNING, MotorCommand.hold("attack", duration_ms=500.0)


def _look_up(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.tap("look_up")


def _walk_forward(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.RUNNING, MotorCommand.hold("forward", duration_ms=400.0)


def _signal_recover(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.idle()


def _signal_navigate(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.idle()


def build_harvesting_tree() -> ReactiveSelector:
    """Construct the HARVESTING state behavior tree."""
    return ReactiveSelector(
        "harvesting_root",
        [
            Sequence(
                "emergency_check",
                [
                    Condition("health_critical", _health_critical),
                    Action("signal_recover", _signal_recover),
                ],
            ),
            Sequence(
                "pickup_drops",
                [
                    Condition("drops_nearby", _drops_nearby),
                    ForceSuccess(
                        "try_pickup",
                        Timeout(
                            "pickup_timeout",
                            Action("walk_to_drop", _walk_forward),
                            timeout_ms=3000.0,
                        ),
                    ),
                ],
            ),
            Sequence(
                "harvest_aligned",
                [
                    ConfidenceGate(
                        "confirm_target",
                        Condition("crosshair_on_target", _crosshair_on_target),
                        required_frames=2,
                    ),
                    Cooldown(
                        "attack_cooldown",
                        Timeout(
                            "attack_timeout",
                            Action("hold_attack", _hold_attack),
                            timeout_ms=8000.0,
                        ),
                        cooldown_ms=500.0,
                    ),
                    Action("look_up_after_break", _look_up),
                ],
            ),
            Selector(
                "align_or_search",
                [
                    Sequence(
                        "search_for_target",
                        [
                            Condition("no_target", _no_target_in_view),
                            Action("signal_navigate", _signal_navigate),
                        ],
                    ),
                ],
            ),
        ],
    )
