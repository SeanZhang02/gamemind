"""Step 1.5 VLM Anchor Validation Spike.

Tests whether Gemma 4 26B can reliably output structured spatial
perception (facing, spatial_context, anchors[]) from game screenshots.

Gate: >= 70% accuracy on anchors → proceed with structured mode.
      < 70% → fall back to text-only mode.

Usage: python scripts/spike_anchor_validation.py
"""

import base64
import io
import json
import sys
import time
from pathlib import Path

import requests
from PIL import Image

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b-a4b-it-q4_K_M"
FIXTURES_DIR = Path("phase-c-0/fixtures")

# Same prompt as the new prompt_builder.py (spatial perception, no action)
SYSTEM_PROMPT = (
    "You observe a game screenshot each tick. Describe what you see in structured JSON.\n"
    "\n"
    "RULES:\n"
    "- Respond with ONLY valid JSON, no other text\n"
    '- The "block" field should be the block type the crosshair is pointing at '
    '(e.g. "oak_log", "stone", "air"), or null if unclear\n'
    "- Be specific about block types -- use the game's native block/object names when possible\n"
    '- The "facing" field describes your camera orientation: '
    '"looking_down", "looking_at_horizon", or "looking_up"\n'
    '- The "anchors" field lists notable objects with their relative direction and distance'
)

USER_PROMPT = (
    "Current subgoal: observe\n"
    "Hints: none\n"
    "\n"
    "Respond with JSON:\n"
    '{"block": "<block_at_crosshair>", '
    '"facing": "<looking_down|looking_at_horizon|looking_up>", '
    '"spatial_context": "<one sentence describing surroundings>", '
    '"anchors": [{"label": "<thing>", "direction": "<ahead|left|right|behind|ahead_left|ahead_right>", '
    '"distance": "<close|medium|far>"}], '
    '"health": <0.0-1.0>, '
    '"entities": [...]}'
)

VALID_FACINGS = {"looking_down", "looking_at_horizon", "looking_up"}
VALID_DIRECTIONS = {"ahead", "ahead_left", "ahead_right", "left", "right", "behind"}
VALID_DISTANCES = {"close", "medium", "far"}


def encode_image(path: Path) -> str:
    img = Image.open(path)
    img = img.resize((512, 288), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def query_vlm(image_b64: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT,
                "images": [image_b64],
            },
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 4096, "num_visual_tokens": 280},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    return json.loads(content)


def validate_response(data: dict, filename: str) -> dict:
    """Score the response on each field."""
    results = {"file": filename, "valid_json": True}

    # facing
    facing = data.get("facing")
    results["facing_present"] = facing is not None
    results["facing_valid"] = facing in VALID_FACINGS
    results["facing_value"] = facing

    # spatial_context
    ctx = data.get("spatial_context")
    results["context_present"] = ctx is not None and len(str(ctx)) > 5
    results["context_value"] = str(ctx)[:80] if ctx else None

    # anchors
    anchors = data.get("anchors", [])
    results["anchors_present"] = isinstance(anchors, list) and len(anchors) > 0
    valid_anchors = 0
    total_anchors = len(anchors) if isinstance(anchors, list) else 0
    if isinstance(anchors, list):
        for a in anchors:
            if not isinstance(a, dict):
                continue
            has_label = bool(a.get("label"))
            has_dir = a.get("direction") in VALID_DIRECTIONS
            has_dist = a.get("distance") in VALID_DISTANCES
            if has_label and has_dir and has_dist:
                valid_anchors += 1
    results["anchors_total"] = total_anchors
    results["anchors_valid"] = valid_anchors
    results["anchors_accuracy"] = valid_anchors / total_anchors if total_anchors > 0 else 0.0
    results["anchors_raw"] = anchors[:3]  # first 3 for inspection

    # block
    results["block_present"] = data.get("block") is not None
    results["block_value"] = data.get("block")

    return results


def main():
    fixtures = sorted(FIXTURES_DIR.glob("*.png"))
    if not fixtures:
        print("No fixtures found!")
        sys.exit(1)

    # Skip UI/menu screenshots (t3_*) -- they don't have spatial content
    game_fixtures = [f for f in fixtures if not f.name.startswith("t3_") and not f.name.startswith("smoke_")]
    print(f"Testing {len(game_fixtures)} game screenshots (skipping UI/menus)\n")

    results = []
    for i, fixture in enumerate(game_fixtures):
        print(f"[{i+1}/{len(game_fixtures)}] {fixture.name}...", end=" ", flush=True)
        t0 = time.time()
        try:
            img_b64 = encode_image(fixture)
            data = query_vlm(img_b64)
            latency = (time.time() - t0) * 1000
            result = validate_response(data, fixture.name)
            result["latency_ms"] = latency
            result["error"] = None
            print(f"{latency:.0f}ms  facing={result['facing_value']}  "
                  f"anchors={result['anchors_valid']}/{result['anchors_total']}  "
                  f"block={result['block_value']}")
        except Exception as e:
            latency = (time.time() - t0) * 1000
            result = {"file": fixture.name, "error": str(e), "latency_ms": latency}
            print(f"ERROR: {e}")
        results.append(result)

    # Summary
    print("\n" + "=" * 70)
    print("STEP 1.5 VALIDATION SPIKE RESULTS")
    print("=" * 70)

    valid = [r for r in results if r.get("error") is None]
    errors = [r for r in results if r.get("error") is not None]

    facing_valid = sum(1 for r in valid if r.get("facing_valid"))
    context_present = sum(1 for r in valid if r.get("context_present"))
    anchors_present = sum(1 for r in valid if r.get("anchors_present"))

    total_anchors = sum(r.get("anchors_total", 0) for r in valid)
    valid_anchors = sum(r.get("anchors_valid", 0) for r in valid)
    anchor_accuracy = valid_anchors / total_anchors * 100 if total_anchors > 0 else 0

    avg_latency = sum(r.get("latency_ms", 0) for r in valid) / len(valid) if valid else 0

    print(f"\nScreenshots tested: {len(game_fixtures)}")
    print(f"Successful parses:  {len(valid)}/{len(game_fixtures)}")
    print(f"Errors:             {len(errors)}")
    print(f"\nFacing valid:       {facing_valid}/{len(valid)} ({facing_valid/len(valid)*100:.0f}%)")
    print(f"Context present:    {context_present}/{len(valid)} ({context_present/len(valid)*100:.0f}%)")
    print(f"Anchors present:    {anchors_present}/{len(valid)} ({anchors_present/len(valid)*100:.0f}%)")
    print(f"Anchor accuracy:    {valid_anchors}/{total_anchors} ({anchor_accuracy:.0f}%)")
    print(f"Avg latency:        {avg_latency:.0f}ms")

    print(f"\n{'=' * 70}")
    if anchor_accuracy >= 70:
        print(f"GATE: PASS ({anchor_accuracy:.0f}% >= 70%) — proceed with structured anchors mode")
    else:
        print(f"GATE: FAIL ({anchor_accuracy:.0f}% < 70%) — fall back to text-only mode")
    print("=" * 70)

    # Detail table
    print("\nDetail:")
    print(f"{'File':<45} {'Facing':<20} {'Anchors':<12} {'Block':<15} {'ms':<6}")
    print("-" * 100)
    for r in valid:
        f = r['file'][:44]
        fac = r.get('facing_value', '?')[:19]
        anc = f"{r.get('anchors_valid', 0)}/{r.get('anchors_total', 0)}"
        blk = str(r.get('block_value', '?'))[:14]
        ms = f"{r.get('latency_ms', 0):.0f}"
        print(f"{f:<45} {fac:<20} {anc:<12} {blk:<15} {ms:<6}")


if __name__ == "__main__":
    main()
