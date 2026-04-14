"""Day 1 sanity test — load Grounding DINO + run on one frame.

Purpose: verify the full pipeline works end-to-end before investing in a
3-thread architecture. Answers:

  1. Does torch cu128 install on Windows + RTX 5090 (sm_120)?
  2. Does transformers GroundingDINO forward pass work without CUDA errors?
  3. Does zero-shot detection produce ANY output on a Minecraft frame?
  4. What is the rough per-frame latency?

Does NOT yet answer: precision/recall on hand-labeled fixtures (Day 2),
UI element detection (Day 6-7), tracking stability (Day 3+).

Usage:
    python -m sanity.detect --fixture PATH --prompts "tree . cow . oak_log"
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 spike — GD sanity test")
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="Path to a single Minecraft screenshot (PNG or JPEG)",
    )
    parser.add_argument(
        "--prompts",
        type=str,
        default="tree . cow . oak_log",
        help=(
            "Space/dot-separated zero-shot text prompts for GD. "
            "Per GD spec, classes are separated by ' . '."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default="IDEA-Research/grounding-dino-tiny",
        help="HuggingFace model id (per research_gd_variants.md Q5)",
    )
    parser.add_argument(
        "--box-threshold",
        type=float,
        default=0.3,
        help="GD box confidence threshold (default 0.3 per HF docs)",
    )
    parser.add_argument(
        "--text-threshold",
        type=float,
        default=0.25,
        help="GD text-token confidence threshold (default 0.25)",
    )
    args = parser.parse_args()

    if not args.fixture.exists():
        print(f"ERROR: fixture not found: {args.fixture}")
        return 2

    # Imports deferred so argparse errors don't wait on heavy ML stack
    print("[1/5] Importing torch + transformers...")
    t0 = time.perf_counter()
    import torch
    from PIL import Image
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    import_dt = time.perf_counter() - t0
    print(f"      imports ok in {import_dt:.2f}s")

    print("[2/5] CUDA / device check...")
    if not torch.cuda.is_available():
        print(f"      torch CUDA NOT available (torch={torch.__version__})")
        print("      ABORT — spike requires GPU inference on 5090")
        return 3
    device_name = torch.cuda.get_device_name(0)
    capability = torch.cuda.get_device_capability(0)
    vram_total = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"      device: {device_name}  (sm_{capability[0]}{capability[1]})")
    print(f"      torch={torch.__version__}  VRAM={vram_total:.1f}GB")
    if capability >= (12, 0):
        print("      [note] Blackwell sm_120 — verifying forward pass works...")

    print(f"[3/5] Loading model {args.model}...")
    t0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(args.model).to("cuda")
    load_dt = time.perf_counter() - t0
    print(f"      loaded in {load_dt:.2f}s")
    vram_after_load = torch.cuda.memory_allocated() / 1e9
    print(f"      VRAM after load: {vram_after_load:.2f}GB")

    print(f"[4/5] Loading fixture: {args.fixture}")
    image = Image.open(args.fixture).convert("RGB")
    print(f"      size: {image.size}")
    print(f"      prompts: {args.prompts!r}")

    print("[5/5] Running inference (10 warmup + 10 timed)...")
    inputs = processor(images=image, text=args.prompts, return_tensors="pt").to("cuda")

    # Warmup
    with torch.no_grad():
        for _ in range(10):
            _ = model(**inputs)
    torch.cuda.synchronize()

    # Timed
    t0 = time.perf_counter()
    with torch.no_grad():
        for _ in range(10):
            outputs = model(**inputs)
    torch.cuda.synchronize()
    avg_dt_ms = (time.perf_counter() - t0) / 10 * 1000

    # Post-process the last output (HF Grounding DINO post-process API)
    # transformers >= 5.x renamed box_threshold -> threshold
    results = processor.post_process_grounded_object_detection(
        outputs,
        inputs.input_ids,
        threshold=args.box_threshold,
        text_threshold=args.text_threshold,
        target_sizes=[image.size[::-1]],  # (H, W)
    )[0]

    vram_peak = torch.cuda.max_memory_allocated() / 1e9

    print("")
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  avg latency per call: {avg_dt_ms:.1f} ms  (target <200ms)")
    print(f"  VRAM peak:            {vram_peak:.2f} GB  (budget <28GB total)")
    print(f"  detections:           {len(results['boxes'])}")
    for i, (box, score, label) in enumerate(
        zip(results["boxes"], results["scores"], results["labels"], strict=False)
    ):
        x1, y1, x2, y2 = box.tolist()
        print(
            f"    [{i}] label={label!r:20s} score={score.item():.3f} "
            f"bbox=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})"
        )

    print("")
    print("NEXT STEPS:")
    if len(results["boxes"]) == 0:
        print("  ⚠️  ZERO detections. Check (a) prompts match visible objects,")
        print("      (b) thresholds not too strict, (c) fixture is real Minecraft frame.")
        print("      If zero on multiple fixtures → kill-switch toward OWLv2.")
    else:
        print("  1. Capture 20+ hand-labeled fixtures (world + UI)")
        print("  2. Compute precision/recall at IoU 0.5 (Day 2 gate)")
        print("  3. Test UI prompt 'inventory slot with item' (Day 6-7 gate)")

    return 0 if len(results["boxes"]) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
