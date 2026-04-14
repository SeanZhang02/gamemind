"""Template matching PoC — demonstrate that Minecraft UI slots can be
localized with zero neural detection, using OpenCV's matchTemplate.

Hypothesis: Minecraft UI is pixel-identical across game sessions at the
same resolution. The inventory_grid panel lives at literally the same
pixels in task3/4/5/5.1/7.1/8. A template cut from one reference fixture
will match every other fixture at ~1.00 correlation with deterministic
position — no model needed, no GPU, <5ms latency.

If this works, `item_in_slot` detection (GD: 0/34 TP) is a solved problem:
once we have the inventory_grid anchor position, slot positions follow by
arithmetic (calibration_constants.yaml).

Usage:
    python -m sanity.template_match_poc

Output:
    - Prints anchor-point match per fixture + confidence score
    - Writes fixtures/overlays_templates/<fixture>_match.png with boxes
      drawn at predicted slot positions (if anchor match > 0.9).
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def extract_template(ref_img_path: Path, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    """Cut a rectangular crop from the reference fixture to use as template."""
    img = cv2.imread(str(ref_img_path))
    if img is None:
        raise SystemExit(f"cannot read {ref_img_path}")
    return img[y1:y2, x1:x2].copy()


def match_one(target_path: Path, template: np.ndarray) -> tuple[float, tuple[int, int]]:
    """Run template matching against one fixture. Returns (best_score, top_left_xy)."""
    img = cv2.imread(str(target_path))
    if img is None:
        return 0.0, (0, 0)
    result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), max_loc


def draw_match_and_slots(
    target_path: Path,
    match_xy: tuple[int, int],
    template_shape: tuple[int, int],
    slot_positions: list[tuple[int, int, int, int, str]],
    output_path: Path,
) -> None:
    """Draw template-match anchor + inferred slot positions."""
    img = cv2.imread(str(target_path))
    th, tw = template_shape[:2]
    x, y = match_xy

    # Anchor rect (red, thick)
    cv2.rectangle(img, (x, y), (x + tw, y + th), (0, 0, 255), 4)
    cv2.putText(img, "TEMPLATE MATCH", (x, max(y - 10, 30)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)

    # Slot rects (blue)
    for sx1, sy1, sx2, sy2, label in slot_positions:
        cv2.rectangle(img, (sx1, sy1), (sx2, sy2), (255, 100, 0), 3)
        cv2.putText(img, label, (sx1 + 4, sy1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 100, 0), 2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)


def main() -> int:
    print("=" * 70)
    print("Template Matching PoC — find inventory panel + slots across fixtures")
    print("=" * 70)

    # Step 1: Extract the inventory-panel template from task4 (Sean's verified
    # inventory_grid bbox = (847, 295)-(1716, 1118), a ~870x820 region).
    ref = FIXTURES / "task4.png"
    if not ref.exists():
        print(f"ERROR: reference {ref} missing")
        return 2

    # Use a smaller distinctive sub-region of the panel to make the template
    # match robust (a larger template has more distinctiveness but may miss
    # if the panel is slightly offset). Take the player avatar area +
    # crafting grid region — roughly the top half of the panel.
    template = extract_template(ref, 847, 295, 1716, 700)
    th, tw = template.shape[:2]
    print(f"\n[1/3] Template extracted from {ref.name}: {tw}x{th} px "
          f"(from inventory_grid top half)")

    # Step 2: Run matchTemplate on all UI fixtures (those we expect to match).
    ui_fixtures = ["task3", "task4", "task5", "task5.1"]
    # 3x3 crafting UI is different layout — expect LOW match score (control):
    different_layout = ["task7.1", "task8"]
    world_fixtures = ["task1", "task2", "task6", "task9", "task10"]

    print(f"\n[2/3] Testing matchTemplate across 11 fixtures...")
    print(f"  Expected HIGH match (same 2x2 inv UI layout): {ui_fixtures}")
    print(f"  Expected LOW match (different 3x3 UI layout):  {different_layout}")
    print(f"  Expected LOW match (world view, no UI):        {world_fixtures}\n")

    scores: dict[str, tuple[float, tuple[int, int]]] = {}
    for name in ui_fixtures + different_layout + world_fixtures:
        p = FIXTURES / f"{name}.png"
        if not p.exists():
            print(f"  SKIP {name}.png (missing)")
            continue
        score, loc = match_one(p, template)
        scores[name] = (score, loc)
        tag = "EXPECTED MATCH" if name in ui_fixtures else (
            "EXPECTED LOW (3x3 UI)" if name in different_layout else "EXPECTED LOW (world)"
        )
        verdict = "✓" if (
            (name in ui_fixtures and score > 0.9)
            or (name not in ui_fixtures and score < 0.9)
        ) else "✗"
        print(f"  {verdict} {name:12s} score={score:.4f} at {loc}   [{tag}]")

    # Step 3: For high-match UI fixtures, overlay predicted slot positions.
    # Slot positions from calibration_constants.yaml (2x2 inv UI):
    #   output_slot:     (1601, 426)-(1700, 520)
    #   crafting_grid_2x2 TL: (1326, 381)-(1411, 461)
    #   crafting_grid_2x2 TR: (1415, 381)-(1500, 461)
    #   crafting_grid_2x2 BL: (1326, 470)-(1411, 550)
    #   crafting_grid_2x2 BR: (1415, 470)-(1500, 550)
    #   hotbar slot 1:   (876, 995)-(961, 1075)
    SLOTS_2x2 = [
        (1601, 426, 1700, 520, "output_slot"),
        (1326, 381, 1411, 461, "craft_TL"),
        (1415, 381, 1500, 461, "craft_TR"),
        (1326, 470, 1411, 550, "craft_BL"),
        (1415, 470, 1500, 550, "craft_BR"),
        (876,  995, 961,  1075, "hotbar_s1"),
    ]

    print(f"\n[3/3] Rendering overlays with predicted slot positions...")
    out_dir = FIXTURES / "overlays_templates"
    for name in ui_fixtures:
        score, loc = scores.get(name, (0.0, (0, 0)))
        if score < 0.9:
            print(f"  SKIP {name} (match score {score:.3f} < 0.9)")
            continue
        # Adjust slot positions by delta between actual match_xy and the
        # expected template position (at (847, 295) in task4). If the
        # panel is at SAME position, delta = (0, 0).
        expected_x, expected_y = 847, 295
        dx, dy = loc[0] - expected_x, loc[1] - expected_y
        adjusted_slots = [
            (x1 + dx, y1 + dy, x2 + dx, y2 + dy, label)
            for (x1, y1, x2, y2, label) in SLOTS_2x2
        ]
        out_path = out_dir / f"{name}_template_match.png"
        draw_match_and_slots(FIXTURES / f"{name}.png", loc, template.shape, adjusted_slots, out_path)
        print(f"  rendered {out_path.relative_to(FIXTURES.parent)}  (match={score:.3f}, dx={dx}, dy={dy})")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ui_matches = sum(1 for n in ui_fixtures if scores.get(n, (0, None))[0] > 0.9)
    false_positives = sum(
        1 for n in different_layout + world_fixtures
        if scores.get(n, (0, None))[0] > 0.9
    )
    print(f"  Correct UI matches:   {ui_matches}/{len(ui_fixtures)}")
    print(f"  False positives:      {false_positives}/{len(different_layout) + len(world_fixtures)}")
    print(f"  Compare to GD-tiny:   inventory_grid F1=0.86 (6/6 TP) — template match should be >=100%")
    print(f"\n  Slot prediction accuracy: each slot bbox from calibration_constants.yaml")
    print(f"  Expect ~100% IoU with labeled slots if panel anchor is correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
