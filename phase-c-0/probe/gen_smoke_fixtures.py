"""Generate synthetic smoke-test fixtures.

These are NOT real Minecraft screenshots. They are text-on-colored-background
images whose correct answer is visible in the image itself, designed only to
verify the probe pipeline (image loading, base64 encoding, Ollama roundtrip,
JSON parsing, scoring) without needing Sean to capture real screenshots yet.

A passing score on these images proves nothing about Minecraft accuracy.
A failing score here means the pipeline itself is broken.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _make(path: Path, bg: tuple[int, int, int], lines: list[str]) -> None:
    img = Image.new("RGB", (640, 480), bg)
    draw = ImageDraw.Draw(img)
    font_big = _font(36)
    font_small = _font(22)

    y = 60
    draw.text((40, y), lines[0], fill=(255, 255, 255), font=font_big)
    y += 60
    for line in lines[1:]:
        draw.text((40, y), line, fill=(230, 230, 230), font=font_small)
        y += 34
    img.save(path)
    print(f"  wrote {path.name}")


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "fixtures"
    out.mkdir(parents=True, exist_ok=True)

    _make(
        out / "smoke_t1.png",
        bg=(96, 96, 96),
        lines=[
            "SMOKE TEST T1",
            "This is a grey synthetic image.",
            "The block in front is: stone",
            "(Not a real screenshot.)",
        ],
    )

    _make(
        out / "smoke_t3.png",
        bg=(20, 20, 20),
        lines=[
            "SMOKE TEST T3",
            "UI state: f3_debug",
            "XYZ: 120.3 / 64.0 / -45.7",
            "FPS: 240  Chunks: 841",
            "Biome: minecraft:plains",
        ],
    )

    _make(
        out / "smoke_t4.png",
        bg=(10, 10, 10),
        lines=[
            "SMOKE TEST T4",
            "Scene: underground cave",
            "Hostiles: none visible",
            "Hazards: none within 5 blocks",
        ],
    )

    groundtruth = [
        {
            "id": "smoke-t1",
            "image": "smoke_t1.png",
            "category": "t1_block",
            "difficulty": "smoke",
            "notes": "Synthetic smoke fixture, answer visible in image text.",
            "expected": {"block": "stone"},
        },
        {
            "id": "smoke-t3",
            "image": "smoke_t3.png",
            "category": "t3_ui",
            "difficulty": "smoke",
            "notes": "Synthetic smoke fixture, F3 overlay simulated.",
            "expected": {"ui_state": "f3_debug"},
        },
        {
            "id": "smoke-t4",
            "image": "smoke_t4.png",
            "category": "t4_spatial",
            "difficulty": "smoke",
            "notes": "Synthetic smoke fixture, underground no-threat scene.",
            "expected": {
                "location": "underground",
                "hostile_visible": False,
                "hazard_visible": False,
                "hazard_type": "none",
            },
        },
    ]

    gt_path = out / "groundtruth.smoke.json"
    gt_path.write_text(json.dumps(groundtruth, indent=2), encoding="utf-8")
    print(f"  wrote {gt_path.name}")


if __name__ == "__main__":
    main()
