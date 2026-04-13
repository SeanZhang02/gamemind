"""NAVIGATING behavior tree — move toward a target location.

Game-agnostic: uses Blackboard's VLM-derived perception to decide
movement, not hardcoded coordinates. Follows Design Rule 1.

Tree structure (ReactiveSelector root):
  1. Emergency check (health critical → signal RECOVERING)
  2. Target reached (close enough → signal HARVESTING)
  3. Target visible approach (walk toward + avoid obstacles)
  4. Target not visible search (rotate + explore)
"""

from __future__ import annotations

from gamemind.blackboard import Blackboard
from gamemind.bt.decorators import ReactiveSelector, Timeout
from gamemind.bt.engine import Action, Condition, Selector, Sequence, Status
from gamemind.bt.motor_command import MotorCommand


def _health_critical(bb: Blackboard) -> bool:
    health = bb.read_value("health", min_confidence=0.3)
    if health is None:
        return False
    try:
        return float(health) < 0.3
    except (ValueError, TypeError):
        return False


def _target_reached(bb: Blackboard) -> bool:
    block = bb.read_value("crosshair_block", min_confidence=0.4)
    if block is None:
        return False
    return block not in ("air", "water", "lava", "")


def _target_visible(bb: Blackboard) -> bool:
    entities = bb.read_value("entities_nearby", min_confidence=0.3)
    vlm_action = bb.read_value("vlm_suggested_action")
    if vlm_action in ("forward", "attack"):
        return True
    return bool(entities and isinstance(entities, list) and len(entities) > 0)


def _path_blocked(bb: Blackboard) -> bool:
    diff = bb.read_value("frame_diff_score", min_confidence=0.3)
    return diff is not None and isinstance(diff, int | float) and diff < 0


def _walk_forward(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.RUNNING, MotorCommand.hold("forward", duration_ms=400.0)


def _jump_forward(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.tap("jump")


def _strafe_left(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.RUNNING, MotorCommand.hold("strafe_left", duration_ms=600.0)


def _rotate_right(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.tap("turn_right")


def _signal_recover(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.idle()


def _signal_arrived(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.idle()


def _signal_replan(_bb: Blackboard) -> tuple[Status, MotorCommand | None]:
    return Status.SUCCESS, MotorCommand.idle()


def build_navigating_tree() -> ReactiveSelector:
    """Construct the NAVIGATING state behavior tree."""
    return ReactiveSelector(
        "navigating_root",
        [
            Sequence(
                "emergency_check",
                [
                    Condition("health_critical", _health_critical),
                    Action("signal_recover", _signal_recover),
                ],
            ),
            Sequence(
                "target_reached",
                [
                    Condition("at_target", _target_reached),
                    Action("signal_arrived", _signal_arrived),
                ],
            ),
            Sequence(
                "approach_visible",
                [
                    Condition("target_visible", _target_visible),
                    Selector(
                        "handle_obstacles",
                        [
                            Sequence(
                                "obstacle_jump",
                                [
                                    Condition("blocked", _path_blocked),
                                    Action("jump", _jump_forward),
                                ],
                            ),
                            Action("walk", _walk_forward),
                        ],
                    ),
                ],
            ),
            Sequence(
                "search_pattern",
                [
                    Timeout(
                        "search_timeout",
                        Selector(
                            "scan_or_explore",
                            [
                                Sequence(
                                    "rotate_scan",
                                    [
                                        Action("rotate", _rotate_right),
                                        Condition("found", _target_visible),
                                    ],
                                ),
                                Sequence(
                                    "walk_explore",
                                    [
                                        Action("walk_explore", _walk_forward),
                                        Action("rotate_after", _rotate_right),
                                    ],
                                ),
                            ],
                        ),
                        timeout_ms=15000.0,
                    ),
                    Action("search_failed_replan", _signal_replan),
                ],
            ),
        ],
    )
