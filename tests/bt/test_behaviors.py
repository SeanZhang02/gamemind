"""BT behavior tests — HARVESTING and NAVIGATING trees with mock Blackboard."""

from __future__ import annotations

from gamemind.blackboard import Blackboard, Producer
from gamemind.bt.engine import Status
from gamemind.bt.harvesting import build_harvesting_tree
from gamemind.bt.navigating import build_navigating_tree


def _bb_with(**slots: object) -> Blackboard:
    bb = Blackboard()
    for key, value in slots.items():
        bb.write(key, value)
    bb.swap()
    return bb


class TestHarvestingTree:
    def test_emergency_fires_on_low_health(self) -> None:
        bb = _bb_with(health=0.1)
        tree = build_harvesting_tree()
        status = tree.tick(bb)
        assert status == Status.SUCCESS
        assert tree.motor_command is not None
        assert tree.motor_command.action_name == ""

    def test_attack_when_crosshair_on_block(self) -> None:
        bb = _bb_with(crosshair_block="oak_log", vlm_suggested_action="attack")
        tree = build_harvesting_tree()
        tree.tick(bb)
        status = tree.tick(bb)
        assert status in (Status.RUNNING, Status.SUCCESS)
        assert tree.motor_command is not None

    def test_signal_navigate_when_no_target(self) -> None:
        bb = _bb_with(crosshair_block="air")
        tree = build_harvesting_tree()
        status = tree.tick(bb)
        assert status == Status.SUCCESS

    def test_pickup_drops_when_items_nearby(self) -> None:
        bb = _bb_with(entities_nearby=["item_drop"], crosshair_block="air")
        tree = build_harvesting_tree()
        status = tree.tick(bb)
        assert status in (Status.RUNNING, Status.SUCCESS)

    def test_full_health_no_emergency(self) -> None:
        bb = _bb_with(health=1.0, crosshair_block="oak_log", vlm_suggested_action="attack")
        tree = build_harvesting_tree()
        tree.tick(bb)
        tree.tick(bb)
        assert tree.motor_command is not None
        assert tree.motor_command.action_name != ""


class TestNavigatingTree:
    def test_emergency_fires_on_low_health(self) -> None:
        bb = _bb_with(health=0.1)
        tree = build_navigating_tree()
        status = tree.tick(bb)
        assert status == Status.SUCCESS

    def test_signals_arrived_when_target_reached(self) -> None:
        bb = _bb_with(crosshair_block="oak_log", health=1.0)
        tree = build_navigating_tree()
        status = tree.tick(bb)
        assert status == Status.SUCCESS

    def test_walks_forward_when_target_visible(self) -> None:
        bb = _bb_with(
            crosshair_block="air",
            health=1.0,
            vlm_suggested_action="forward",
        )
        tree = build_navigating_tree()
        status = tree.tick(bb)
        assert status == Status.RUNNING
        assert tree.motor_command is not None
        assert tree.motor_command.action_name == "forward"

    def test_jumps_when_blocked(self) -> None:
        bb = Blackboard()
        bb.write("crosshair_block", "air")
        bb.write("health", 1.0)
        bb.write("vlm_suggested_action", "forward")
        bb.write("frame_diff_score", -1.0, Producer.WATCHDOG)
        bb.swap()
        tree = build_navigating_tree()
        status = tree.tick(bb)
        assert status == Status.SUCCESS
        assert tree.motor_command is not None
        assert tree.motor_command.action_name == "jump"

    def test_rotates_when_target_not_visible(self) -> None:
        bb = _bb_with(crosshair_block="air", health=1.0)
        tree = build_navigating_tree()
        tree.tick(bb)
        assert tree.motor_command is not None
