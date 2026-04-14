"""Render labelme-format JSON as bbox overlay PNG for quick visual QA.

Purpose: Sean verifies pre-labels without installing labelme GUI. Just
open the overlay PNG in any image viewer and eyeball whether my bboxes
look right.

Usage:
    uv run python -m sanity.draw_labels --fixtures-dir fixtures --labels-dir fixtures/labels --output-dir fixtures/overlays

Output: one PNG per fixture with bboxes drawn + labels in corners.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Muted color palette per class (approximate; falls back to hash-based color)
_CLASS_COLORS = {
    "oak_log": (184, 115, 51),      # bronze
    "tree": (34, 139, 34),          # forest green
    "leaves": (0, 200, 0),          # bright green
    "grass_block": (100, 180, 100), # grass
    "crafting_table": (139, 69, 19),  # brown
    "wooden_pickaxe": (218, 165, 32), # goldenrod
    "inventory_grid": (220, 20, 60),  # crimson
    "crafting_grid_2x2": (255, 140, 0),  # orange
    "crafting_grid_3x3": (255, 165, 0),  # orange2
    "output_slot": (255, 20, 147),  # deep pink
    "inventory_slot": (180, 100, 180),  # purple
    "item_in_slot": (30, 144, 255),  # dodger blue
    "hotbar": (255, 215, 0),         # gold
    "health_bar": (255, 0, 0),       # red
    "hunger_bar": (160, 82, 45),     # sienna
}


def color_for(label: str) -> tuple[int, int, int]:
    if label in _CLASS_COLORS:
        return _CLASS_COLORS[label]
    # Deterministic fallback from label hash
    h = abs(hash(label)) % (256 * 256 * 256)
    return (h % 256, (h >> 8) % 256, (h >> 16) % 256)


def draw(fixture_path: Path, labels_json: Path, output_path: Path) -> None:
    with labels_json.open("r", encoding="utf-8") as fh:
        label_data = json.load(fh)
    img = Image.open(fixture_path).convert("RGB")
    draw_ctx = ImageDraw.Draw(img, "RGBA")

    # Try to load a font; fall back to default if system font missing
    font = None
    for font_path in [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(font_path).exists():
            font = ImageFont.truetype(font_path, 28)
            break
    if font is None:
        font = ImageFont.load_default()

    for shape in label_data.get("shapes", []):
        label = shape.get("label", "?")
        pts = shape.get("points", [])
        if len(pts) < 2:
            continue
        (x1, y1), (x2, y2) = pts[0], pts[1]
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        color = color_for(label)
        # Bbox outline (4px wide)
        draw_ctx.rectangle([x1, y1, x2, y2], outline=color + (255,), width=4)
        # Semi-transparent fill for visibility
        draw_ctx.rectangle([x1, y1, x2, y2], fill=color + (40,))
        # Label text above top-left
        text_y = max(0, y1 - 36)
        # Background strip behind text for readability
        text_w, text_h = draw_ctx.textbbox((0, 0), label, font=font)[2:]
        draw_ctx.rectangle(
            [x1, text_y, x1 + text_w + 12, text_y + text_h + 8],
            fill=color + (220,),
        )
        draw_ctx.text((x1 + 6, text_y + 4), label, fill=(255, 255, 255), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw labelme bboxes on fixtures for visual QA")
    parser.add_argument("--fixtures-dir", type=Path, default=Path("fixtures"))
    parser.add_argument("--labels-dir", type=Path, default=Path("fixtures/labels"))
    parser.add_argument("--output-dir", type=Path, default=Path("fixtures/overlays"))
    args = parser.parse_args()

    if not args.labels_dir.exists():
        print(f"ERROR: labels dir not found: {args.labels_dir}")
        return 2

    json_files = sorted(args.labels_dir.glob("*.json"))
    print(f"Found {len(json_files)} label JSON file(s) in {args.labels_dir}")

    rendered = 0
    skipped = 0
    for lbl_path in json_files:
        with lbl_path.open("r", encoding="utf-8") as fh:
            try:
                d = json.load(fh)
            except Exception as exc:
                print(f"  SKIP (parse error) {lbl_path.name}: {exc}")
                skipped += 1
                continue
        rel = d.get("imagePath", "")
        fixture_path = (lbl_path.parent / rel).resolve()
        if not fixture_path.exists():
            # Fallback: search by JSON stem in fixtures-dir
            fallback = args.fixtures_dir / f"{lbl_path.stem}.png"
            if fallback.exists():
                fixture_path = fallback
            else:
                print(f"  SKIP (fixture missing) {lbl_path.name}")
                skipped += 1
                continue
        out_path = args.output_dir / f"{lbl_path.stem}_overlay.png"
        draw(fixture_path, lbl_path, out_path)
        print(f"  rendered -> {out_path}")
        rendered += 1

    print(f"\nRendered {rendered} overlay(s), skipped {skipped}.")
    print(f"Open {args.output_dir} to inspect.")
    return 0 if rendered > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
