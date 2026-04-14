"""Phase 1 spike — VRAM co-load benchmark (Layer 1 + Layer 2).

Purpose: validate the **co-load VRAM gate** of `phase-c/spike/README.md`:

    Peak VRAM < 28 GB with Grounding DINO-tiny + Qwen3-VL 8B q4_K_M
    co-loaded on RTX 5090 32GB, sustained for 10 minutes of alternating
    inference.

Why this matters: the two-layer hybrid perception design (final-design.md)
assumes both models can be resident simultaneously. We have measured
GD-tiny alone at 2.11 GB peak (Day 1 sanity) and Ollama documents
qwen3-vl:8b q4_K_M as ~6 GB, but **fragmentation, allocator thrash, and
KV cache growth under sustained alternation are unmeasured**. This is the
last gate before Phase 2 commits to the architecture.

Procedure
---------

  t=0          nvidia-smi baseline (no model loaded)
  t=load_gd    load GD-tiny -> CUDA, 5 warmup forwards, log VRAM
  t=load_qwen  HTTP POST to Ollama /api/generate with a vision request
               (this triggers the Ollama runner to map the GGUF into VRAM),
               log VRAM after first response
  t=0..600s    every 30s alternate:
                 odd ticks: GD inference on a random fixture
                 even ticks: Ollama vision request on same fixture
               sample nvidia-smi each tick
  t=600s       final VRAM, compute peak/mean/final, write JSON report

VRAM is read via `nvidia-smi --query-gpu=memory.used` because torch's
`max_memory_allocated` does NOT see Ollama's allocations (separate
process). nvidia-smi sees the whole device.

Gate: peak_used_mb < 28672 (28 GiB). 32 GB device with 4 GB buffer for
OS / driver / display compositor.

Usage
-----

    cd phase-c/spike
    uv run python -m sanity.benchmark_vram

Output: phase-c/spike/reports/benchmark_vram.json

Exit codes: 0 = gate PASS, 1 = gate FAIL, 2 = blocker (Ollama unreachable
or model missing — does NOT auto-pull, surfaces to caller).
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---- Configuration -----------------------------------------------------------

OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "qwen3-vl:8b-instruct-q4_K_M"
GD_MODEL = "IDEA-Research/grounding-dino-tiny"
GD_PROMPTS = "tree . cow . oak_log . inventory slot"

DURATION_S = 600          # 10 minutes sustained
SAMPLE_INTERVAL_S = 30    # one nvidia-smi sample every 30s -> 21 samples
GATE_PEAK_MB = 28_672     # 28 GiB

# Fixture pool — random selection per tick, same fixture sent to both models
SPIKE_DIR = Path(__file__).resolve().parent.parent
FIXTURE_DIR = SPIKE_DIR / "fixtures"
REPORT_PATH = SPIKE_DIR / "reports" / "benchmark_vram.json"


# ---- VRAM probing ------------------------------------------------------------


def query_vram_mb() -> tuple[int, int]:
    """Return (used_mb, free_mb) from nvidia-smi.

    Works across processes (sees Ollama runner + python torch).
    """
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    line = out.stdout.strip().splitlines()[0]
    used_str, free_str = (s.strip() for s in line.split(","))
    return int(used_str), int(free_str)


# ---- Ollama --------------------------------------------------------------------


def check_ollama_ready(httpx_module: Any) -> tuple[bool, str]:
    """Return (ready, reason). ready=True iff daemon up AND model present."""
    try:
        r = httpx_module.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"ollama unreachable at {OLLAMA_HOST}: {e}"
    tags = r.json()
    models = [m.get("name", "") for m in tags.get("models", [])]
    # Ollama may report tag with or without ":latest"; check prefix match
    if not any(m == OLLAMA_MODEL or m.startswith(OLLAMA_MODEL + ":") for m in models):
        return False, f"model {OLLAMA_MODEL!r} not in ollama list. Available: {models}"
    return True, "ok"


def ollama_vision_request(httpx_module: Any, image_b64: str) -> dict[str, Any]:
    """Single non-streaming vision request. Returns parsed response dict.

    First call after model not loaded triggers GGUF -> VRAM map; subsequent
    calls reuse the warm runner (Ollama default keep_alive 5min). We pass
    keep_alive='10m' to ensure model stays resident across our 10-min loop.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": "Describe this image in 5 words.",
        "images": [image_b64],
        "stream": False,
        "keep_alive": "10m",
        "options": {"num_predict": 16},  # cap output, we only care about VRAM
    }
    r = httpx_module.post(
        f"{OLLAMA_HOST}/api/generate",
        json=payload,
        timeout=120.0,  # cold load can be ~30s on first call
    )
    r.raise_for_status()
    return r.json()


# ---- Main --------------------------------------------------------------------


def write_report(report: dict[str, Any], reason: str = "") -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if reason:
        report["aborted_reason"] = reason
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[report] wrote {REPORT_PATH}")


def main() -> int:
    # ---- Stage 0: imports + fixtures + Ollama precheck ----
    print(f"[t=0] benchmark start  {datetime.now(timezone.utc).isoformat()}")

    fixtures = sorted(p for p in FIXTURE_DIR.glob("task*.png") if p.is_file())
    if not fixtures:
        print(f"BLOCKER: no task*.png fixtures in {FIXTURE_DIR}")
        return 2
    print(f"[t=0] {len(fixtures)} fixtures found")

    print("[t=0] importing httpx / torch / transformers / PIL...")
    import base64
    import io

    import httpx
    import torch
    from PIL import Image
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    if not torch.cuda.is_available():
        print("BLOCKER: torch CUDA not available")
        return 2
    print(
        f"[t=0] device={torch.cuda.get_device_name(0)}  "
        f"sm_{torch.cuda.get_device_capability(0)[0]}{torch.cuda.get_device_capability(0)[1]}  "
        f"torch={torch.__version__}"
    )

    print("[t=0] checking Ollama daemon + model availability...")
    ready, reason = check_ollama_ready(httpx)
    if not ready:
        print(f"BLOCKER: {reason}")
        print("        (per task spec: do NOT auto-pull. report blocker to caller.)")
        # write a stub report so the failure mode is visible in CI/PR
        write_report(
            {
                "duration_s": 0,
                "samples": [],
                "vram_mb": {},
                "gate_peak_mb": GATE_PEAK_MB,
                "gate_status": "BLOCKED",
                "fragmentation_signs": "n/a",
                "ollama_model": OLLAMA_MODEL,
                "notes": reason,
            },
            reason=reason,
        )
        return 2
    print(f"[t=0] ollama OK, model {OLLAMA_MODEL} present")

    samples: list[dict[str, Any]] = []
    bench_t0 = time.perf_counter()

    def t_s() -> float:
        return time.perf_counter() - bench_t0

    def sample(phase: str, note: str = "") -> int:
        used, free = query_vram_mb()
        rec = {"t_s": round(t_s(), 2), "used_mb": used, "free_mb": free, "phase": phase}
        if note:
            rec["note"] = note
        samples.append(rec)
        print(f"  [vram] t={rec['t_s']:>6.1f}s  used={used:>6d}MB  free={free:>6d}MB  ({phase})")
        return used

    # ---- Stage 1: baseline ----
    baseline_mb = sample("baseline")

    # ---- Stage 2: load GD-tiny ----
    print(f"[t={t_s():.1f}] loading Grounding DINO-tiny...")
    gd_processor = AutoProcessor.from_pretrained(GD_MODEL)
    gd_model = AutoModelForZeroShotObjectDetection.from_pretrained(GD_MODEL).to("cuda")
    # warmup
    warm_img = Image.open(fixtures[0]).convert("RGB")
    warm_inputs = gd_processor(images=warm_img, text=GD_PROMPTS, return_tensors="pt").to("cuda")
    with torch.no_grad():
        for _ in range(5):
            _ = gd_model(**warm_inputs)
    torch.cuda.synchronize()
    after_gd_mb = sample("after_gd_load")

    # ---- Stage 3: load Qwen3-VL via Ollama ----
    print(f"[t={t_s():.1f}] triggering Ollama qwen3-vl load (first vision request)...")
    with fixtures[0].open("rb") as f:
        warm_b64 = base64.b64encode(f.read()).decode("ascii")
    cold_t0 = time.perf_counter()
    try:
        resp = ollama_vision_request(httpx, warm_b64)
    except Exception as e:  # noqa: BLE001
        print(f"BLOCKER: Ollama vision request failed: {e}")
        write_report(
            {
                "duration_s": round(t_s(), 1),
                "samples": samples,
                "vram_mb": {"baseline": baseline_mb, "after_gd": after_gd_mb},
                "gate_peak_mb": GATE_PEAK_MB,
                "gate_status": "BLOCKED",
                "fragmentation_signs": "n/a",
                "ollama_model": OLLAMA_MODEL,
                "notes": f"ollama generate failed: {e}",
            },
            reason=str(e),
        )
        return 2
    cold_load_dt = time.perf_counter() - cold_t0
    print(f"        first response in {cold_load_dt:.1f}s, {len(resp.get('response', ''))}B reply")
    after_qwen_mb = sample("after_qwen_load", note=f"cold_load_dt={cold_load_dt:.1f}s")

    # ---- Stage 4: 10-min sustained alternation ----
    print(f"[t={t_s():.1f}] entering {DURATION_S}s sustained loop "
          f"(sample every {SAMPLE_INTERVAL_S}s)")
    rng = random.Random(42)
    loop_start = t_s()
    tick = 0
    next_sample_t = loop_start + SAMPLE_INTERVAL_S
    last_log_t = loop_start
    while True:
        elapsed = t_s() - loop_start
        if elapsed >= DURATION_S:
            break

        # alternate workload
        fixture = rng.choice(fixtures)
        is_gd_tick = (tick % 2 == 0)

        try:
            if is_gd_tick:
                img = Image.open(fixture).convert("RGB")
                inputs = gd_processor(
                    images=img, text=GD_PROMPTS, return_tensors="pt"
                ).to("cuda")
                with torch.no_grad():
                    _ = gd_model(**inputs)
                torch.cuda.synchronize()
                workload = "gd"
            else:
                with fixture.open("rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                _ = ollama_vision_request(httpx, b64)
                workload = "qwen"
        except Exception as e:  # noqa: BLE001
            # don't abort the whole bench — note and continue. OOM during
            # sustained loop IS the gate signal we want to capture.
            note = f"workload_error tick={tick} kind={'gd' if is_gd_tick else 'qwen'}: {e}"
            print(f"  [warn] {note}")
            samples.append({
                "t_s": round(t_s(), 2),
                "used_mb": -1,
                "free_mb": -1,
                "phase": "error",
                "note": note,
            })
            workload = "error"

        tick += 1

        # sample VRAM every SAMPLE_INTERVAL_S
        now = t_s()
        if now >= next_sample_t:
            sample("sustained", note=f"tick={tick} last_workload={workload}")
            next_sample_t += SAMPLE_INTERVAL_S
        elif now - last_log_t >= 10:
            last_log_t = now
            print(f"  [tick] t={now - loop_start:>6.1f}s/{DURATION_S}s  "
                  f"ticks={tick}  last={workload}")

    final_mb = sample("final")

    # ---- Stage 5: aggregate ----
    sustained = [
        s["used_mb"]
        for s in samples
        if s["phase"] == "sustained" and s["used_mb"] >= 0
    ]
    all_used = [s["used_mb"] for s in samples if s["used_mb"] >= 0]
    peak_mb = max(all_used) if all_used else -1
    mean_sustained_mb = (sum(sustained) / len(sustained)) if sustained else -1

    # crude fragmentation heuristic: if (peak - final) > 1 GB, allocator
    # likely retained free blocks not returned to driver. Surface for human review.
    frag_delta = peak_mb - final_mb if peak_mb >= 0 and final_mb >= 0 else 0
    if frag_delta > 1024:
        frag_signs = (
            f"peak {peak_mb}MB but final {final_mb}MB — {frag_delta}MB held by allocator"
        )
    else:
        frag_signs = "none"

    gate_status = "PASS" if 0 < peak_mb < GATE_PEAK_MB else "FAIL"

    error_count = sum(1 for s in samples if s["phase"] == "error")

    report = {
        "schema_version": 1,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(t_s(), 1),
        "duration_loop_s": round(t_s() - loop_start, 1),
        "sample_interval_s": SAMPLE_INTERVAL_S,
        "tick_count": tick,
        "error_tick_count": error_count,
        "samples": samples,
        "vram_mb": {
            "baseline": baseline_mb,
            "after_gd": after_gd_mb,
            "after_qwen": after_qwen_mb,
            "peak": peak_mb,
            "mean_sustained": round(mean_sustained_mb, 1),
            "final": final_mb,
        },
        "gate_peak_mb": GATE_PEAK_MB,
        "gate_status": gate_status,
        "fragmentation_signs": frag_signs,
        "ollama_model": OLLAMA_MODEL,
        "gd_model": GD_MODEL,
        "notes": (
            f"Workload alternates GD/Qwen ticks; nvidia-smi sampled every {SAMPLE_INTERVAL_S}s. "
            f"Errors during loop = {error_count}. Cold-load Ollama latency captured in "
            f"after_qwen_load sample note."
        ),
    }
    write_report(report)

    print()
    print("=" * 60)
    print(f"GATE: {gate_status}   peak={peak_mb}MB / budget={GATE_PEAK_MB}MB")
    print(f"  baseline={baseline_mb}MB  after_gd={after_gd_mb}MB  "
          f"after_qwen={after_qwen_mb}MB  final={final_mb}MB")
    print(f"  mean_sustained={mean_sustained_mb:.1f}MB  fragmentation={frag_signs}")
    print(f"  ticks={tick}  errors={error_count}")
    print("=" * 60)

    return 0 if gate_status == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[abort] KeyboardInterrupt")
        sys.exit(130)
