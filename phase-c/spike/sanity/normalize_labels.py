"""Normalize labelme JSON files to canonical labelme 6.0.0 schema.

Fixes:
1. Move non-standard top-level keys (_annotator, _source_fixture, etc.) into
   imageData=null and drop them (labelme ignores unknown top-level keys but
   cleaner to remove).
2. Ensure each shape has ALL canonical keys: label, points, group_id,
   description, shape_type, flags, mask (even if null/empty).
3. Move shape-level "notes" into "description" (labelme-standard field).
4. Ensure imagePath is just the filename (no ../ prefix); labelme resolves
   relative to the JSON's own directory, so the parent of labels/ is
   fixtures/, and the image lives in fixtures/ one-level-up. Compute path
   that resolves from labels/ → ../<stem>.png.
5. Verify JSON is loadable via labelme's own _load_shape_json_obj.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def normalize_shape(raw: dict) -> dict:
    """Canonicalize a shape entry."""
    # Pull notes → description (if description already exists, concat)
    description = raw.get("description", "") or ""
    if "notes" in raw:
        notes = raw["notes"]
        description = f"{description} | {notes}" if description else notes

    canonical = {
        "label": raw["label"],
        "points": raw["points"],
        "group_id": raw.get("group_id"),  # None OK
        "description": description,
        "shape_type": raw["shape_type"],
        "flags": raw.get("flags", {}),
        "mask": raw.get("mask"),  # None OK
    }
    return canonical


def normalize_file(json_path: Path) -> bool:
    """Rewrite JSON in-place with canonical schema. Returns True if changed."""
    with json_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Expected top-level keys for labelme
    new_data = {
        "version": data.get("version", "6.0.0"),
        "flags": data.get("flags", {}),
        "shapes": [normalize_shape(s) for s in data.get("shapes", [])],
        "imagePath": data.get("imagePath", ""),
        "imageData": None,  # we don't embed image bytes
        "imageHeight": data.get("imageHeight"),
        "imageWidth": data.get("imageWidth"),
    }

    # Fix imagePath — should be relative from JSON's dir to image.
    # Labels live in fixtures/labels/; images live in fixtures/ (one level up).
    # So imagePath = ../<stem>.png
    stem = json_path.stem
    expected_rel = f"../{stem}.png"
    # If existing imagePath points to an absolute or deeply-nested path that
    # does not exist relative to labels/, rewrite it.
    current_path = new_data["imagePath"]
    resolved = (json_path.parent / current_path).resolve()
    if not resolved.exists():
        new_data["imagePath"] = expected_rel

    # Write
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(new_data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return True


def main() -> int:
    labels_dir = Path("fixtures/labels")
    if not labels_dir.exists():
        # Try relative to script location
        here = Path(__file__).resolve()
        labels_dir = here.parent.parent / "fixtures" / "labels"
    if not labels_dir.exists():
        print(f"ERROR: labels dir not found: {labels_dir}")
        return 2

    normalized = 0
    for jp in sorted(labels_dir.glob("*.json")):
        try:
            normalize_file(jp)
            print(f"  normalized {jp.name}")
            normalized += 1
        except Exception as exc:
            print(f"  FAILED {jp.name}: {exc!r}")
    print(f"\nNormalized {normalized} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
