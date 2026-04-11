"""Task definitions for the Phase C-0 probe.

Four task categories (T1-T4) stress-test different aspects of vision-language
capability we need for Minecraft:
  T1 block_id      - identify the block in front of the crosshair
  T2 inventory     - read hotbar contents
  T3 ui_state      - classify game UI state
  T4 spatial       - reason about immediate surroundings

Each task has a prompt template, JSON format contract, and a scoring function
that returns a float in [0.0, 1.0].
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Task:
    category: str
    prompt: str
    score_fn: Callable[[dict[str, Any], dict[str, Any]], float]


def _norm(s: Any) -> str:
    return str(s).strip().lower().replace(" ", "_")


def score_t1_block(pred: dict[str, Any], gt: dict[str, Any]) -> float:
    return 1.0 if _norm(pred.get("block")) == _norm(gt.get("block")) else 0.0


def score_t2_inventory(pred: dict[str, Any], gt: dict[str, Any]) -> float:
    gt_slots = {s["slot"]: s for s in gt.get("hotbar", [])}
    pred_slots = {
        s.get("slot"): s for s in pred.get("hotbar", []) if isinstance(s, dict)
    }
    if not gt_slots:
        return 0.0
    correct = 0
    for slot, gt_entry in gt_slots.items():
        p = pred_slots.get(slot)
        if p is None:
            continue
        gt_item = gt_entry.get("item")
        p_item = p.get("item")
        if gt_item is None and p_item is None:
            correct += 1
            continue
        if _norm(p_item) == _norm(gt_item):
            if gt_item is None or p.get("count") == gt_entry.get("count"):
                correct += 1
    return correct / len(gt_slots)


def score_t3_ui(pred: dict[str, Any], gt: dict[str, Any]) -> float:
    return 1.0 if _norm(pred.get("ui_state")) == _norm(gt.get("ui_state")) else 0.0


def score_t4_spatial(pred: dict[str, Any], gt: dict[str, Any]) -> float:
    fields = ["location", "hostile_visible", "hazard_visible", "hazard_type"]
    total = len(fields)
    correct = 0
    for f in fields:
        gt_v = gt.get(f)
        p_v = pred.get(f)
        if isinstance(gt_v, bool) or isinstance(p_v, bool):
            if bool(gt_v) == bool(p_v):
                correct += 1
        else:
            if _norm(p_v) == _norm(gt_v):
                correct += 1
    return correct / total


TASKS: dict[str, Task] = {
    "t1_block": Task(
        category="t1_block",
        prompt=(
            "You are looking at a Minecraft first-person screenshot. "
            "Identify the block type directly in front of the player crosshair "
            "(the center of the screen). Use the canonical Minecraft block id "
            "(e.g. oak_log, stone, grass_block, cobblestone, iron_ore). "
            'If the crosshair is pointing at air or sky, answer "air". '
            "Respond with ONLY valid JSON matching this schema: "
            '{"block": "<block_id>"}'
        ),
        score_fn=score_t1_block,
    ),
    "t2_inventory": Task(
        category="t2_inventory",
        prompt=(
            "You are looking at a Minecraft screenshot showing the player hotbar "
            "at the bottom of the screen. Read the 9 hotbar slots from left (slot 1) "
            "to right (slot 9). For each slot, report the item id and count. "
            "Use canonical Minecraft item ids (e.g. oak_planks, stick, wooden_pickaxe). "
            "Empty slots must be reported with item=null and count=0. "
            "Respond with ONLY valid JSON matching this schema: "
            '{"hotbar": [{"slot": 1, "item": "<id_or_null>", "count": <int>}, ...9 entries]}'
        ),
        score_fn=score_t2_inventory,
    ),
    "t3_ui": Task(
        category="t3_ui",
        prompt=(
            "You are looking at a Minecraft screenshot. Classify the current UI state. "
            "Choose EXACTLY ONE value from this set, matching by the visual features below. "
            "If more than one could apply, choose the most specific container label. "
            "\n\n"
            "Definitions (match by visual features, not by intent):\n"
            "- hud_only: normal gameplay view, only hotbar + health/hunger bars visible, "
            "no window or panel overlayed on top of the world.\n"
            "- inventory_open: the player pressed E. A window is open showing a small "
            "**2x2 crafting grid** + a character model on the left + 4 armor slots + "
            "the player inventory grid. NO 3x3 grid, NO 'Chest' label.\n"
            "- crafting_table: the player right-clicked a crafting table. A window is "
            "open showing a **3x3 crafting grid** + result slot + player inventory. "
            "The key signal is the 3x3 grid (not 2x2).\n"
            "- furnace: window with the word 'Furnace' at the top, an input slot, a fuel "
            "slot below it, and a flame arrow pointing to an output slot on the right.\n"
            "- chest: window with the word 'Chest' at the top, a horizontal grid of slots "
            "(9 columns wide, 1-6 rows), and the player inventory below. The explicit "
            "'Chest' label distinguishes this from inventory_open.\n"
            "- main_menu: the Minecraft title screen shown BEFORE loading any world. "
            "Large 'Minecraft' logo, 'Singleplayer' / 'Multiplayer' / 'Options' buttons. "
            "No game world visible behind.\n"
            "- pause_menu: the player pressed Esc inside a loaded world. A centered panel "
            "titled 'Game Menu' with buttons 'Back to Game', 'Advancements', 'Statistics', "
            "'Options', 'Save and Quit to Title'. The game world is visible blurred behind.\n"
            "- f3_debug: gameplay view with the F3 debug text overlay in the top-left "
            "(coordinates XYZ, FPS graph, biome, chunk info). NO window is open.\n"
            "- chat_open: gameplay view with a chat input field at the bottom of the "
            "screen, usually with a blinking cursor or the character '>'.\n"
            "- death_screen: large red 'You Died!' text in the center, with 'Respawn' "
            "and 'Title Screen' buttons below.\n"
            "\n"
            "Respond with ONLY valid JSON matching this schema: "
            '{"ui_state": "<one_value>"}'
        ),
        score_fn=score_t3_ui,
    ),
    "t4_spatial": Task(
        category="t4_spatial",
        prompt=(
            "You are looking at a Minecraft first-person screenshot. Reason about "
            "the immediate spatial context. Answer four questions: "
            "(1) Is the player currently underground, above_ground, or underwater? "
            "(2) Is a hostile mob (zombie, skeleton, creeper, spider, etc.) visible? "
            "(3) Is there an obvious hazard (lava, sheer cliff, void) within ~5 blocks? "
            "(4) If a hazard is visible, what type: lava, cliff, void, or none. "
            "Respond with ONLY valid JSON matching this schema: "
            '{"location": "<underground|above_ground|underwater>", '
            '"hostile_visible": <true|false>, '
            '"hazard_visible": <true|false>, '
            '"hazard_type": "<lava|cliff|void|none>"}'
        ),
        score_fn=score_t4_spatial,
    ),
}
