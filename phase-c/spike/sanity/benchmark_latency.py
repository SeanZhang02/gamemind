"""Phase 1 spike — Grounding DINO single-frame latency benchmark.

Purpose: validate the gate "p90 latency <= 200 ms/frame on RTX 5090" claimed
by Day 1 sanity, which only ran 10 timed iterations on a single fixture and
DID NOT actually verify the p90 distribution. HuggingFace public issue #31533
reports 378–528 ms for GD-tiny forward — we may already be past gate without
knowing it.

Methodology:
  * Round-robin over all task*.png fixtures in phase-c/spike/fixtures/.
  * 100 warmup frames (untimed) — let CUDA / cudnn autotune converge.
  * 100 timed frames — measure each forward pass via time.perf_counter()
    with torch.cuda.synchronize() bracketing every call (otherwise CUDA
    kernels run async and we'd be timing kernel launches, not execution).
  * Use the same prompt vocabulary the eval harness uses (world group
    joined by " . " per GD spec) so the cost we measure is representative
    of real Phase 2 inference, not a degenerate 1-token prompt.
  * Outputs latency_ms.{p50,p90,p95,mean,max,min} + gate verdict to
    phase-c/spike/reports/benchmark_latency.json.

Usage:
    uv run python -m sanity.benchmark_latency
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


# Default world prompt group from fixtures/prompts.yaml. Hardcoded (rather than
# YAML-parsed) so this benchmark has no extra deps and the prompt cost is
# reproducible / auditable from the script alone. Keep in sync if prompts.yaml
# changes meaningfully.
WORLD_PROMPT = (
    "tree . oak_log . leaves . grass_block . stone . iron_ore . "
    "crafting_table . cow . zombie . creeper"
)

GATE_P90_MS = 200.0
N_WARMUP_DEFAULT = 100
N_TIMED_DEFAULT = 100


def percentile(samples: list[float], pct: float) -> float:
    """Linear-interpolated percentile, no numpy dependency."""
    if not samples:
        raise ValueError("empty samples")
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 spike — GD-tiny latency benchmark (RTX 5090 gate verification)"
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "fixtures",
        help="Directory containing task*.png fixtures",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "reports"
        / "benchmark_latency.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="IDEA-Research/grounding-dino-tiny",
    )
    parser.add_argument("--n-warmup", type=int, default=N_WARMUP_DEFAULT)
    parser.add_argument("--n-timed", type=int, default=N_TIMED_DEFAULT)
    parser.add_argument(
        "--prompt",
        type=str,
        default=WORLD_PROMPT,
        help="GD prompt string (classes joined by ' . ' per GD spec)",
    )
    args = parser.parse_args()

    # Collect fixtures (task*.png at the top level of fixtures/, not in
    # subdirs — those are labels/overlays).
    fixtures = sorted(args.fixtures_dir.glob("task*.png"))
    if not fixtures:
        print(f"ERROR: no task*.png in {args.fixtures_dir}", file=sys.stderr)
        return 2
    print(f"Found {len(fixtures)} fixtures:")
    for f in fixtures:
        print(f"  - {f.name}")

    # Heavy imports deferred so argparse errors fail fast.
    print("\n[1/4] Importing torch + transformers...")
    t0 = time.perf_counter()
    import torch
    from PIL import Image
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    print(f"      imports ok in {time.perf_counter() - t0:.2f}s")

    print("\n[2/4] CUDA / device check...")
    if not torch.cuda.is_available():
        print(f"ERROR: torch CUDA not available (torch={torch.__version__})", file=sys.stderr)
        return 3
    device_name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    vram_total = torch.cuda.get_device_properties(0).total_memory / 1e9
    print(f"      device: {device_name}  (sm_{cap[0]}{cap[1]})")
    print(f"      torch={torch.__version__}  VRAM={vram_total:.1f}GB")

    print(f"\n[3/4] Loading model {args.model}...")
    t0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(args.model).to("cuda")
    model.eval()
    print(f"      loaded in {time.perf_counter() - t0:.2f}s")
    vram_after_load = torch.cuda.memory_allocated() / 1e9
    print(f"      VRAM after load: {vram_after_load:.2f}GB")

    # Pre-load + pre-process all fixtures once. We re-process per call below
    # to mimic real Phase 2 cost (a new frame arrives → process → forward).
    images = [Image.open(p).convert("RGB") for p in fixtures]
    print(f"      loaded {len(images)} images, sizes: {set(im.size for im in images)}")

    n_warmup = args.n_warmup
    n_timed = args.n_timed
    print(f"\n[4/4] Benchmark: {n_warmup} warmup + {n_timed} timed (round-robin)...")

    def step(idx: int) -> float:
        """One processor+forward step. Returns latency_ms (cuda-sync'd)."""
        img = images[idx % len(images)]
        inputs = processor(images=img, text=args.prompt, return_tensors="pt").to("cuda")
        torch.cuda.synchronize()
        t = time.perf_counter()
        with torch.no_grad():
            _ = model(**inputs)
        torch.cuda.synchronize()
        return (time.perf_counter() - t) * 1000.0

    # Warmup — discard timings.
    print(f"      warmup ({n_warmup} frames)...", flush=True)
    for i in range(n_warmup):
        _ = step(i)
        if (i + 1) % 25 == 0:
            print(f"        warmup {i + 1}/{n_warmup}")

    # Timed.
    print(f"      timed ({n_timed} frames)...", flush=True)
    latencies: list[float] = []
    for i in range(n_timed):
        latencies.append(step(i))
        if (i + 1) % 25 == 0:
            print(
                f"        timed {i + 1}/{n_timed}  "
                f"running p90={percentile(latencies, 90):.1f}ms"
            )

    vram_peak = torch.cuda.max_memory_allocated() / 1e9

    # Stats.
    p50 = percentile(latencies, 50)
    p90 = percentile(latencies, 90)
    p95 = percentile(latencies, 95)
    mean = statistics.mean(latencies)
    mx = max(latencies)
    mn = min(latencies)

    gate_status = "PASS" if p90 <= GATE_P90_MS else "FAIL"

    report = {
        "model": "grounding-dino-tiny",
        "model_id": args.model,
        "device": "cuda",
        "device_name": device_name,
        "compute_capability": f"sm_{cap[0]}{cap[1]}",
        "torch_version": torch.__version__,
        "vram_total_gb": round(vram_total, 2),
        "vram_after_load_gb": round(vram_after_load, 2),
        "vram_peak_gb": round(vram_peak, 2),
        "n_warmup": n_warmup,
        "n_timed": n_timed,
        "fixtures_used": [f.name for f in fixtures],
        "prompt": args.prompt,
        "latency_ms": {
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "p95": round(p95, 2),
            "mean": round(mean, 2),
            "max": round(mx, 2),
            "min": round(mn, 2),
        },
        "gate_p90_ms": GATE_P90_MS,
        "gate_status": gate_status,
        "notes": (
            "Round-robin over fixtures with per-call torch.cuda.synchronize() "
            "bracketing the forward pass. Includes processor() preprocessing "
            "cost per call to mimic Phase 2 frame ingestion. Compare against "
            "HF issue #31533 reports of 378-528ms on consumer GPUs."
        ),
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))

    print("")
    print("=" * 70)
    print("LATENCY BENCHMARK RESULTS")
    print("=" * 70)
    print(f"  device:        {device_name} (sm_{cap[0]}{cap[1]})")
    print(f"  model:         {args.model}")
    print(f"  fixtures:      {len(fixtures)}  (round-robin)")
    print(f"  warmup/timed:  {n_warmup} / {n_timed}")
    print(f"  prompt tokens: {len(args.prompt.split(' . '))} classes")
    print("")
    print(f"  p50:  {p50:7.1f} ms")
    print(f"  p90:  {p90:7.1f} ms   (gate = {GATE_P90_MS:.0f} ms)")
    print(f"  p95:  {p95:7.1f} ms")
    print(f"  mean: {mean:7.1f} ms")
    print(f"  min:  {mn:7.1f} ms")
    print(f"  max:  {mx:7.1f} ms")
    print("")
    print(f"  VRAM peak: {vram_peak:.2f} GB")
    print("")
    print(f"  GATE: {gate_status}  (p90 {p90:.1f} ms vs gate {GATE_P90_MS:.0f} ms)")
    print("")
    print(f"  report written → {args.report}")

    return 0 if gate_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
