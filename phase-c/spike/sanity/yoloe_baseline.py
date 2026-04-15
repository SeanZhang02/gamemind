"""Phase C spike — YOLOE-L zero-shot baseline (Track C).

Purpose
-------
Day-2 GD-tiny gave micro F1 = 0.152 on 11 hand-labeled Minecraft fixtures
with three total failures (item_in_slot 0/34, hotbar 0/13, tree 0/22).
OWLv2 (Track B) was worse — F1 0.049 — refuting the "just swap the CLIP
detector" hypothesis. detector-researcher survey landed on YOLOE (ICCV
2025, THU-MIG) as the single remaining open-vocab candidate with a
plausibly different failure mode:

    - YOLOv10 backbone (not BERT / not CLIP), ~161 FPS on T4 → est. 2-4 ms
      on RTX 5090 sm_120. 10-100x faster than GD-tiny multi-pass (229 ms).
    - +10 LVIS AP over YOLO-World-L zero-shot.
    - RepRTA **visual-prompt** path: arbitrary-exemplar image prompts folded
      into the main model at inference with zero runtime penalty. This is
      the architecturally-different lever vs text-prompted GD / OWLv2 that
      might unstick the `item_in_slot` 0/34 wall.

This script is the de-risk gate. Three kill-switches (any one → spike stops,
stay on GD path):

    1. Blackwell sm_120 load fails   (weight load, dtype, cuda init)
    2. Single-image warm latency ≥ 50 ms on 5090
    3. 11-fixture micro F1 < 0.10   (worse than GD Day-2 at 0.152)

If ALL three pass → proceed to integration. If only #3 passes, the latency
headroom alone is not worth the integration cost — we already have GD
working.

Pipeline (per fixture)
----------------------
1. Load .png + sibling labelme .json (reuse eval_harness loaders).
2. Build the combined class vocabulary from prompts.yaml (union of
   world + ui — same as owlv2_baseline.py + GD --all-classes baseline).
3. Configure YOLOE with the vocabulary via `set_classes(names, text_pe)`
   — Ultralytics' one-time prompt embedding injection, re-used across
   all 11 images so we measure STEADY-STATE inference, not prompt setup.
4. 10 warmup forwards + 20 timed forwards on a reference fixture (task1).
   Warm single-image latency = median of the 20. Cold = first forward.
5. For each of 11 fixtures, one forward at score_threshold, collect
   bboxes, run same greedy IoU≥0.5 matching as GD / OWLv2 — reuses
   eval_harness.greedy_match so numbers are bit-identical.
6. Write reports/yoloe_baseline.json in the SAME schema as
   owlv2_baseline.json (per_class / micro / macro / vs_gd_tiny / verdict)
   plus a new `kill_switch` block for the three gates.

Stretch — YOLOE visual-prompt mode
----------------------------------
Off by default behind `--visual-prompt-stretch` flag. When enabled AND
task4.png overlay crop exists, run a SECOND eval using `predict(
visual_prompts=...)` on the `item_in_slot` class only. This exercises
the RepRTA path. If the stretch run closes the 0/34 gap where text-prompt
didn't, report `visual_prompt_signal: track_b_resurrection`.

Design decisions
----------------
- **Reuse eval_harness primitives** (Bbox, PerClassStats, _finalize,
  greedy_match, load_labelme_json, load_prompts_yaml,
  build_combined_vocab, print_table). Zero reimpl → numbers directly
  comparable to eval_day2.json and owlv2_baseline.json.
- **Class names stay snake_case in YOLOE**. Ultralytics YOLOE tokenizes
  via CLIP and tolerates underscores better than GD's BERT. We do NOT
  pass natural-language phrases like OWLv2 — the YOLOE README shows
  raw class names. If recall is terrible, main agent can flip to
  phrase-mode in a follow-up (one-line change in `_configure_classes`).
- **Single-pass combined vocab.** YOLOE's text-prompt cost is O(vocab)
  embedding lookup done ONCE at set_classes, not per forward. No
  multi-pass needed — direct latency win vs GD.
- **Same IoU 0.5, same score threshold 0.2** as GD + OWLv2 baselines.
  Apples-to-apples with `vs_gd_tiny` comparison block.
- **No NMS override**. Ultralytics applies NMS internally. Unlike OWLv2
  (which fires densely at low thresholds so we added nms_class_wise),
  YOLOE's YOLOv10 backbone does NMS-free detection via dual label
  assignment per the paper. Trust the native output.
- **No fine-tuning**. Zero-shot only. Same spec constraint as Tracks A/B.

What this script intentionally does NOT do
------------------------------------------
- **Does not modify eval_harness.py** or any existing file.
- **Does not add ultralytics to pyproject.toml.** Main agent decides.
  If the import fails the script exits with a clear BLOCKER message.
- **Does not retry downloads.** First HF / Ultralytics hub failure →
  SystemExit. No patch-on-patch loops.
- **Does not commit / push / run.** Main agent runs.

Usage
-----
    uv run python -m sanity.yoloe_baseline \\
        --fixtures-dir fixtures \\
        --labels-dir   fixtures/labels \\
        --prompts-yaml fixtures/prompts.yaml \\
        --output-json  reports/yoloe_baseline.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from sanity.eval_harness import (  # type: ignore
    Bbox,
    ImageReport,
    PerClassStats,
    _finalize,
    build_combined_vocab,
    greedy_match,
    load_labelme_json,
    load_prompts_yaml,
    print_table,
)

# Baseline numbers from reports/eval_day2.json — cached here so the
# `vs_gd_tiny` comparison is stable across re-runs. Mirrors
# owlv2_baseline._GD_TINY_BASELINE exactly.
_GD_TINY_BASELINE: dict[str, Any] = {
    "item_in_slot": {"tp": 0, "fn": 34},
    "hotbar": {"tp": 0, "fn": 13},
    "tree": {"tp": 0, "fn": 22},
    "micro_f1": 0.152,
}

# Kill-switch thresholds from team-lead spec (2026-04-14).
_KS_MAX_WARM_LATENCY_MS = 50.0
_KS_MIN_MICRO_F1 = 0.10

# Reference fixture used for latency measurement — same seed every run for
# reproducibility. task1 = forest scene, 1920x1080, representative of the
# steady-state workload. If task1 is removed, main agent should pick any
# stable fixture of similar resolution.
_LATENCY_REF_FIXTURE = "task1.png"
_LATENCY_WARMUP_ITERS = 10
_LATENCY_TIMED_ITERS = 20


# ---------------------------------------------------------------------------
# YOLOE wrapper
# ---------------------------------------------------------------------------


class YOLOERunner:
    """Thin wrapper around Ultralytics YOLOE. Load once, `set_classes` once,
    predict many.

    Mirrors the shape of GDRunner / OWLv2Runner so the eval loop reads
    identically. Differences:
      - text-prompt configuration happens ONCE via set_classes, not per
        forward. This is the main latency win vs GD.
      - Output parsing goes through ultralytics Results objects, not HF
        processor post-processing.
      - Device / dtype explicitly set; if sm_120 load fails we surface a
        kill-switch RED FLAG rather than silently falling back to CPU.
    """

    def __init__(self, weights_path: str, device: str = "cuda:0") -> None:
        # Deferred imports — keeps --help snappy, surfaces missing-dep
        # errors as clean blockers rather than mid-eval crashes.
        try:
            import torch
        except ImportError as e:
            raise SystemExit(
                f"BLOCKER: torch not available: {e}\n"
                "  Expected `torch>=2.9.0` from pyproject (cu128 index).\n"
                "  Do NOT `uv add` — report to main agent."
            ) from e

        try:
            from ultralytics import YOLOE
        except ImportError as e:
            raise SystemExit(
                "BLOCKER: ultralytics package not installed.\n"
                "  Required for YOLOE (ICCV 2025). Add to "
                "phase-c/spike/pyproject.toml:\n"
                "      \"ultralytics>=8.3.0\",   # YOLOE support\n"
                "  then `uv sync`.\n"
                "  Do NOT `uv add` from this script — main agent "
                "handles dependency decisions.\n"
                f"  (raw error: {e})"
            ) from e

        if not torch.cuda.is_available():
            raise SystemExit(
                "BLOCKER: CUDA required for YOLOE spike — GPU not available.\n"
                "  Kill-switch #1 (sm_120 load) trivially fails."
            )

        # Record GPU capability so the kill-switch report can distinguish
        # "sm_120 load failed because wrong GPU" vs "weights load crashed".
        props = torch.cuda.get_device_properties(0)
        self.device_name = props.name
        self.cuda_capability = f"sm_{props.major}{props.minor}"

        self.torch = torch
        self.device = device
        self.weights_path = weights_path
        self.configured_classes: list[str] = []

        # Weight load. Ultralytics handles auto-download from GitHub
        # releases on first use; if offline and cache missing, it errors
        # out — we turn that into a SystemExit BLOCKER.
        try:
            self.model = YOLOE(weights_path)
        except Exception as e:
            raise SystemExit(
                f"BLOCKER: YOLOE weight load failed ({weights_path}): {e}\n"
                "  Kill-switch #1 RED FLAG.\n"
                "  Check: (1) ultralytics version supports YOLOE, "
                "(2) weights path / hub reachable, (3) disk space."
            ) from e

        # Move the underlying torch module to cuda explicitly so we catch
        # Blackwell sm_120 incompatibility as a clean error rather than
        # during the first forward. Ultralytics usually defers device
        # placement to .predict(device=...) but we want the failure mode
        # surfaced here.
        try:
            self.model.to(device)
        except Exception as e:
            raise SystemExit(
                f"BLOCKER: YOLOE .to({device}) failed on {self.device_name} "
                f"({self.cuda_capability}): {e}\n"
                "  Kill-switch #1 RED FLAG — likely Blackwell sm_120 "
                "incompat in current torch/ultralytics combo."
            ) from e

    def configure_classes(self, class_names: list[str]) -> None:
        """Inject the vocabulary via YOLOE's RepRTA text path.

        set_classes recomputes text embeddings once and folds them into
        the detection head. After this call, inference is pure YOLOv10
        forward — zero text-encoding overhead per image. This is the
        architectural reason YOLOE can hit real-time while staying
        zero-shot.

        Classes are the canonical snake_case names from prompts.yaml.
        We pass them as-is (no natural-language phrasing). YOLOE's CLIP
        text encoder handles underscores better than GD's BERT; if
        recall is catastrophic, a follow-up can try phrase-mode.
        """
        try:
            # Ultralytics API: set_classes(names, text_pe) where text_pe
            # is the pre-computed prompt embedding from get_text_pe(names).
            text_pe = self.model.get_text_pe(class_names)
            self.model.set_classes(class_names, text_pe)
        except AttributeError as e:
            raise SystemExit(
                f"BLOCKER: YOLOE API mismatch — set_classes/get_text_pe "
                f"unavailable on this ultralytics build: {e}\n"
                "  Required: ultralytics>=8.3.0 (YOLOE integrated). "
                "Check `pip show ultralytics`."
            ) from e
        except Exception as e:
            raise SystemExit(
                f"BLOCKER: YOLOE set_classes failed for vocab "
                f"size={len(class_names)}: {e}"
            ) from e
        self.configured_classes = list(class_names)

    def run(self, image, score_threshold: float) -> list[Bbox]:
        """Single forward → list[Bbox]. Relies on configure_classes having
        been called first (enforced by assertion — spike code, not lib).
        """
        assert self.configured_classes, (
            "YOLOERunner.run called before configure_classes — "
            "this is a script bug, not a runtime condition"
        )

        # ultralytics accepts PIL directly; we pass verbose=False to keep
        # the per-frame stdout clean.
        results = self.model.predict(
            image,
            conf=score_threshold,
            device=self.device,
            verbose=False,
        )
        if not results:
            return []
        r = results[0]

        out: list[Bbox] = []
        if r.boxes is None:
            return out

        # r.boxes.xyxy : Tensor(N, 4) in pixel coords
        # r.boxes.conf : Tensor(N,)
        # r.boxes.cls  : Tensor(N,) integer class indices
        # r.names      : dict[int, str] (set_classes updates this mapping)
        xyxy = r.boxes.xyxy.detach().cpu().tolist()
        conf = r.boxes.conf.detach().cpu().tolist()
        cls = r.boxes.cls.detach().cpu().tolist()

        for (x1, y1, x2, y2), score, label_idx in zip(xyxy, conf, cls, strict=False):
            idx = int(label_idx)
            if idx < 0 or idx >= len(self.configured_classes):
                continue
            out.append(
                Bbox(
                    label=self.configured_classes[idx],
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    score=float(score),
                )
            )
        return out


# ---------------------------------------------------------------------------
# Latency benchmark (kill-switch #2)
# ---------------------------------------------------------------------------


def measure_latency(
    runner: YOLOERunner,
    ref_image_path: Path,
    score_threshold: float,
    warmup: int = _LATENCY_WARMUP_ITERS,
    timed: int = _LATENCY_TIMED_ITERS,
) -> dict[str, float]:
    """Cold + warm single-image latency. Synchronizes CUDA around the
    timed region so we measure GPU work, not async launch overhead.

    Cold = first forward (includes kernel autotune / graph build).
    Warm = median of `timed` forwards after `warmup` forwards.
    """
    from PIL import Image

    image = Image.open(ref_image_path).convert("RGB")
    torch = runner.torch

    # Cold forward. CUDA sync after to flush.
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    runner.run(image, score_threshold)
    torch.cuda.synchronize()
    cold_ms = (time.perf_counter() - t0) * 1000.0

    # Warmup
    for _ in range(warmup):
        runner.run(image, score_threshold)
    torch.cuda.synchronize()

    # Timed samples — individually synced so median is clean.
    samples: list[float] = []
    for _ in range(timed):
        torch.cuda.synchronize()
        t = time.perf_counter()
        runner.run(image, score_threshold)
        torch.cuda.synchronize()
        samples.append((time.perf_counter() - t) * 1000.0)

    return {
        "cold_ms": round(cold_ms, 2),
        "warm_median_ms": round(statistics.median(samples), 2),
        "warm_mean_ms": round(statistics.fmean(samples), 2),
        "warm_p95_ms": round(_quantile(samples, 0.95), 2),
        "warm_min_ms": round(min(samples), 2),
        "warm_max_ms": round(max(samples), 2),
        "n_samples": timed,
    }


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


# ---------------------------------------------------------------------------
# Eval loop (mirrors owlv2_baseline.evaluate)
# ---------------------------------------------------------------------------


def evaluate(
    fixtures_dir: Path,
    labels_dir: Path,
    prompts: dict[str, list[str]],
    runner: YOLOERunner,
    score_threshold: float,
    iou_threshold: float,
    verbose: bool,
) -> dict[str, Any]:
    """Run YOLOE on every fixture with a sibling .json GT."""
    per_class: dict[str, PerClassStats] = {}
    image_reports: list[ImageReport] = []

    fixtures = sorted(fixtures_dir.glob("*.png")) + sorted(fixtures_dir.glob("*.jpg"))
    if not fixtures:
        raise SystemExit(f"no fixtures found under {fixtures_dir}")

    combined_vocab = build_combined_vocab(prompts)
    if verbose:
        print(f"  [vocab] {len(combined_vocab)} classes: {combined_vocab}")

    # One-time vocab injection — see YOLOERunner.configure_classes docstring.
    runner.configure_classes(combined_vocab)

    from PIL import Image

    for img_path in fixtures:
        label_path = labels_dir / f"{img_path.stem}.json"
        if not label_path.exists():
            if verbose:
                print(f"  [skip] {img_path.name}: no GT at {label_path}")
            continue

        gts = load_labelme_json(label_path)
        image = Image.open(img_path).convert("RGB")

        t0 = time.perf_counter()
        preds = runner.run(image, score_threshold)
        dt_ms = (time.perf_counter() - t0) * 1000

        matches, matched_p, matched_g = greedy_match(gts, preds, iou_threshold)

        for pi, _gi, i_iou in matches:
            cls = preds[pi].label
            per_class.setdefault(cls, PerClassStats()).tp += 1
            per_class[cls].iou_sum += i_iou
        for pi, p in enumerate(preds):
            if pi not in matched_p:
                per_class.setdefault(p.label, PerClassStats()).fp += 1
        for gi, g in enumerate(gts):
            if gi not in matched_g:
                per_class.setdefault(g.label, PerClassStats()).fn += 1

        rep = ImageReport(
            image=img_path.name,
            prompt_group="combined",
            n_gt=len(gts),
            n_pred=len(preds),
            tp=len(matches),
            fp=len(preds) - len(matched_p),
            fn=len(gts) - len(matched_g),
        )
        if verbose:
            print(
                f"  [{img_path.name}] gt={rep.n_gt} pred={rep.n_pred} "
                f"TP={rep.tp} FP={rep.fp} FN={rep.fn} t={dt_ms:.0f}ms"
            )
        image_reports.append(rep)

    return _finalize(per_class, image_reports, iou_threshold, score_threshold)


# ---------------------------------------------------------------------------
# Visual-prompt stretch mode
# ---------------------------------------------------------------------------


def run_visual_prompt_stretch(
    runner: YOLOERunner,
    fixtures_dir: Path,
    labels_dir: Path,
    score_threshold: float,
    iou_threshold: float,
) -> dict[str, Any]:
    """Stretch experiment: visual-prompt YOLOE on `item_in_slot` only.

    Cuts 3-5 exemplar crops from task4.png (inventory scene) using its
    labelme GT boxes, feeds them as visual prompts to YOLOE, re-runs
    prediction on all fixtures, measures item_in_slot per-class stats.

    The RepRTA visual path should NOT depend on class name at all at
    inference — only the exemplars matter. If this closes the 0/34 gap
    that text-prompt YOLOE didn't, it's the track-B-resurrection signal
    flagged by detector-researcher.

    Defensive: if task4.png or its labels file is missing, or if YOLOE's
    predict() doesn't accept visual_prompts on this ultralytics build,
    returns a skipped-mode dict rather than crashing the main run.
    """
    task4_img = fixtures_dir / "task4.png"
    task4_lbl = labels_dir / "task4.json"
    if not task4_img.exists() or not task4_lbl.exists():
        return {"status": "skipped", "reason": "task4 fixture missing"}

    try:
        from PIL import Image
    except ImportError as e:
        return {"status": "skipped", "reason": f"PIL import: {e}"}

    # Collect up to 5 item_in_slot exemplar boxes.
    task4_gts = load_labelme_json(task4_lbl)
    item_boxes = [g for g in task4_gts if g.label == "item_in_slot"][:5]
    if not item_boxes:
        return {"status": "skipped", "reason": "no item_in_slot boxes in task4"}

    # Visual prompts in ultralytics YOLOE are pixel boxes + per-box class
    # ids. All exemplars belong to class 0 ("item_in_slot") since this is
    # a single-class visual eval.
    bboxes = [[b.x1, b.y1, b.x2, b.y2] for b in item_boxes]
    cls_ids = [0] * len(item_boxes)
    visual_prompts = {"bboxes": bboxes, "cls": cls_ids}
    ref_image = Image.open(task4_img).convert("RGB")

    per_class: dict[str, PerClassStats] = {}
    try:
        fixtures = sorted(fixtures_dir.glob("task*.png"))
        for img_path in fixtures:
            label_path = labels_dir / f"{img_path.stem}.json"
            if not label_path.exists():
                continue
            gts = [g for g in load_labelme_json(label_path) if g.label == "item_in_slot"]
            if not gts and img_path.name != "task4.png":
                # Skip fixtures with no item_in_slot GT — no signal.
                continue

            image = Image.open(img_path).convert("RGB")
            # Ultralytics YOLOE visual-prompt API takes a refer_image
            # (source of the visual prompts) distinct from the target.
            results = runner.model.predict(
                image,
                refer_image=ref_image,
                visual_prompts=visual_prompts,
                predictor=_get_visual_predictor_cls(),
                conf=score_threshold,
                verbose=False,
            )
            if not results or results[0].boxes is None:
                preds: list[Bbox] = []
            else:
                r = results[0]
                xyxy = r.boxes.xyxy.detach().cpu().tolist()
                conf = r.boxes.conf.detach().cpu().tolist()
                # All preds are class 0 = item_in_slot in this mode.
                preds = [
                    Bbox(
                        label="item_in_slot",
                        x1=float(x1),
                        y1=float(y1),
                        x2=float(x2),
                        y2=float(y2),
                        score=float(s),
                    )
                    for (x1, y1, x2, y2), s in zip(xyxy, conf, strict=False)
                ]

            matches, matched_p, matched_g = greedy_match(gts, preds, iou_threshold)
            for pi, _gi, i_iou in matches:
                per_class.setdefault("item_in_slot", PerClassStats()).tp += 1
                per_class["item_in_slot"].iou_sum += i_iou
            for pi in range(len(preds)):
                if pi not in matched_p:
                    per_class.setdefault("item_in_slot", PerClassStats()).fp += 1
            for gi in range(len(gts)):
                if gi not in matched_g:
                    per_class.setdefault("item_in_slot", PerClassStats()).fn += 1
    except Exception as e:
        # Most likely failure: ultralytics build doesn't expose
        # YOLOEVPSegPredictor or visual_prompts kwarg. Not a blocker —
        # the text-prompt eval is the primary signal.
        return {
            "status": "skipped",
            "reason": f"visual-prompt API unavailable or failed: {e}",
        }

    s = per_class.get("item_in_slot", PerClassStats())
    total_gt = s.tp + s.fn
    closed = s.tp > 0
    return {
        "status": "ran",
        "exemplar_count": len(item_boxes),
        "item_in_slot_tp": s.tp,
        "item_in_slot_fp": s.fp,
        "item_in_slot_fn": s.fn,
        "item_in_slot_score": f"{s.tp}/{total_gt}" if total_gt else "0/0",
        "visual_prompt_signal": (
            "track_b_resurrection" if closed else "no_signal"
        ),
    }


def _get_visual_predictor_cls():
    """Deferred import for the visual-prompt predictor class. Ultralytics
    names it YOLOEVPSegPredictor (detection weights still use the seg
    predictor for visual-prompt path, per official README example).

    Kept in its own function so ImportError here gets caught inside
    run_visual_prompt_stretch's try/except and downgrades to a skip.
    """
    from ultralytics.models.yolo.yoloe.predict_vp import YOLOEVPSegPredictor
    return YOLOEVPSegPredictor


# ---------------------------------------------------------------------------
# vs_gd_tiny block + kill-switch evaluation
# ---------------------------------------------------------------------------


def _format_class_score(per_class: dict[str, dict[str, Any]], cls: str) -> str:
    s = per_class.get(cls)
    if s is None:
        return "0/0 (class absent)"
    tp = s["tp"]
    fn = s["fn"]
    return f"{tp}/{tp + fn}"


def _build_vs_gd(report: dict[str, Any]) -> dict[str, Any]:
    pc = report["per_class"]
    micro_f1 = report["micro"]["f1"]
    return {
        "item_in_slot_gd": "0/34",
        "item_in_slot_yoloe": _format_class_score(pc, "item_in_slot"),
        "hotbar_gd": "0/13",
        "hotbar_yoloe": _format_class_score(pc, "hotbar"),
        "tree_gd": "0/22",
        "tree_yoloe": _format_class_score(pc, "tree"),
        "micro_f1_gd": _GD_TINY_BASELINE["micro_f1"],
        "micro_f1_yoloe": micro_f1,
    }


def _evaluate_kill_switches(
    latency: dict[str, float],
    micro_f1: float | None,
    device_name: str,
    cuda_capability: str,
) -> dict[str, Any]:
    """Apply the three gates. Returns structured pass/fail per gate plus
    an overall verdict string that the script prints in the footer.
    """
    # Gate 1: sm_120 load — if we got here, the runner initialized, which
    # means the weight load + .to(cuda) both succeeded. Failures surface
    # earlier as SystemExit BLOCKERs. Record actuals for the report.
    gate1 = {
        "name": "sm_120_load",
        "passed": True,
        "device": device_name,
        "cuda_capability": cuda_capability,
        "note": (
            "Runner init succeeded. If the actual GPU is NOT sm_120 "
            "(Blackwell), this gate is vacuous for the 5090 target — "
            "re-run on the real hardware."
        ),
    }

    warm = latency.get("warm_median_ms", float("nan"))
    gate2 = {
        "name": "warm_latency_under_50ms",
        "threshold_ms": _KS_MAX_WARM_LATENCY_MS,
        "measured_warm_median_ms": warm,
        "passed": (warm < _KS_MAX_WARM_LATENCY_MS),
    }

    f1_val = micro_f1 if micro_f1 is not None else float("nan")
    gate3 = {
        "name": "micro_f1_at_or_above_0.10",
        "threshold": _KS_MIN_MICRO_F1,
        "measured": f1_val,
        "passed": (isinstance(f1_val, int | float) and f1_val >= _KS_MIN_MICRO_F1),
    }

    all_passed = gate1["passed"] and gate2["passed"] and gate3["passed"]
    if all_passed:
        verdict = (
            "ALL GREEN — YOLOE clears all 3 kill-switches. "
            "Proceed to integration PoC."
        )
    else:
        failures = [g["name"] for g in (gate1, gate2, gate3) if not g["passed"]]
        verdict = (
            f"RED FLAG on {', '.join(failures)} — spike stops, stay on GD path."
        )

    return {
        "gates": [gate1, gate2, gate3],
        "all_passed": all_passed,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase C spike — YOLOE zero-shot baseline (Track C)"
    )
    parser.add_argument("--fixtures-dir", type=Path, default=Path("fixtures"))
    parser.add_argument("--labels-dir", type=Path, default=Path("fixtures/labels"))
    parser.add_argument("--prompts-yaml", type=Path, default=Path("fixtures/prompts.yaml"))
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/yoloe_baseline.json"),
    )
    # YOLOE-L (large) per detector-researcher recommendation — best
    # zero-shot LVIS AP of the open-weight YOLOE family. `-seg` variant
    # is the flavor the official README uses in text-prompt examples; it
    # still supports pure detection via `predict()`. If main agent
    # prefers the pure-detection variant, flip to `yoloe-v8l.pt` here.
    parser.add_argument(
        "--weights",
        type=str,
        default="yoloe-v8l-seg.pt",
        help=(
            "Ultralytics YOLOE weights path. Default yoloe-v8l-seg.pt "
            "(auto-downloads on first use). Alternatives: yoloe-v8l.pt "
            "(det-only), yoloe-11l-seg.pt (YOLO11 backbone variant)."
        ),
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.2,
        help="Apples-to-apples with GD and OWLv2 baselines (also 0.2).",
    )
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument(
        "--visual-prompt-stretch",
        action="store_true",
        help=(
            "Optional: run a second eval using YOLOE's visual-prompt "
            "(RepRTA) path on `item_in_slot` only, using task4.png "
            "exemplars. Flagged as experimental; closes the 0/34 gap "
            "if YOLOE's visual-prompt mode generalizes to UI primitives."
        ),
    )
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()

    for p in (args.fixtures_dir, args.labels_dir, args.prompts_yaml):
        if not p.exists():
            print(f"ERROR: path not found: {p}", file=sys.stderr)
            return 2

    prompts = load_prompts_yaml(args.prompts_yaml)
    if not prompts:
        print(f"ERROR: no groups in {args.prompts_yaml}", file=sys.stderr)
        return 2

    print(f"[1/4] Loading YOLOE weights {args.weights}...")
    t0 = time.perf_counter()
    runner = YOLOERunner(args.weights)
    print(
        f"      loaded in {time.perf_counter() - t0:.1f}s on "
        f"{runner.device_name} ({runner.cuda_capability})"
    )

    # Configure classes ONCE before latency measurement, so the warm
    # numbers reflect steady-state inference exactly as the eval loop
    # will use it.
    combined_vocab = build_combined_vocab(prompts)
    runner.configure_classes(combined_vocab)
    print(f"      configured {len(combined_vocab)} classes via set_classes")

    ref_path = args.fixtures_dir / _LATENCY_REF_FIXTURE
    if not ref_path.exists():
        # Fallback: first .png alphabetically. Script is resilient to
        # task1 being renamed / removed but keeps the default stable.
        pngs = sorted(args.fixtures_dir.glob("*.png"))
        if not pngs:
            print(f"ERROR: no fixtures for latency ref in {args.fixtures_dir}")
            return 2
        ref_path = pngs[0]

    print(f"[2/4] Measuring latency on {ref_path.name} "
          f"({_LATENCY_WARMUP_ITERS} warmup + {_LATENCY_TIMED_ITERS} timed)...")
    latency = measure_latency(
        runner,
        ref_path,
        args.score_threshold,
        warmup=_LATENCY_WARMUP_ITERS,
        timed=_LATENCY_TIMED_ITERS,
    )
    print(
        f"      cold={latency['cold_ms']}ms  warm_median={latency['warm_median_ms']}ms  "
        f"p95={latency['warm_p95_ms']}ms  (n={latency['n_samples']})"
    )

    print(f"[3/4] Evaluating fixtures in {args.fixtures_dir}...")
    eval_report = evaluate(
        fixtures_dir=args.fixtures_dir,
        labels_dir=args.labels_dir,
        prompts=prompts,
        runner=runner,
        score_threshold=args.score_threshold,
        iou_threshold=args.iou_threshold,
        verbose=args.verbose,
    )

    vs_gd = _build_vs_gd(eval_report)
    micro_f1 = eval_report["micro"]["f1"]
    kill_switch = _evaluate_kill_switches(
        latency=latency,
        micro_f1=micro_f1,
        device_name=runner.device_name,
        cuda_capability=runner.cuda_capability,
    )

    visual_prompt_report: dict[str, Any] = {"status": "not_requested"}
    if args.visual_prompt_stretch:
        print("[3.5/4] Running visual-prompt stretch (item_in_slot only)...")
        visual_prompt_report = run_visual_prompt_stretch(
            runner=runner,
            fixtures_dir=args.fixtures_dir,
            labels_dir=args.labels_dir,
            score_threshold=args.score_threshold,
            iou_threshold=args.iou_threshold,
        )
        print(f"        visual_prompt: {visual_prompt_report}")

    final_report: dict[str, Any] = {
        "model": args.weights,
        "model_family": "YOLOE (ICCV 2025, THU-MIG)",
        "n_fixtures": len(eval_report["images"]),
        "iou_threshold": args.iou_threshold,
        "score_threshold": args.score_threshold,
        "latency": latency,
        "per_class": eval_report["per_class"],
        "micro": eval_report["micro"],
        "macro": eval_report["macro"],
        "vs_gd_tiny": vs_gd,
        "kill_switch": kill_switch,
        "visual_prompt_stretch": visual_prompt_report,
        "notes": (
            "Single-pass union-vocab YOLOE zero-shot (text-prompt path "
            "via set_classes/get_text_pe). Same IoU=0.5 greedy matching "
            "as GD and OWLv2 baselines (eval_harness primitives reused "
            "verbatim). Score threshold 0.2 apples-to-apples. YOLOE "
            "does NMS-free detection via dual label assignment, so no "
            "post-NMS applied. Visual-prompt stretch is gated on "
            "--visual-prompt-stretch flag and task4.png availability."
        ),
        "_images": eval_report["images"],
    }

    print(f"[4/4] Writing report to {args.output_json}...")
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

    print_table(eval_report)
    print("")
    print("=" * 78)
    print("LATENCY (warm median vs 50ms kill-switch)")
    print("=" * 78)
    for k, v in latency.items():
        print(f"  {k:<22} {v}")
    print("")
    print("=" * 78)
    print("VS GROUNDING DINO TINY (Day 2 baseline)")
    print("=" * 78)
    for k, v in vs_gd.items():
        print(f"  {k:<22} {v}")
    print("")
    print("=" * 78)
    print("KILL-SWITCH GATES")
    print("=" * 78)
    for gate in kill_switch["gates"]:
        status = "PASS" if gate["passed"] else "FAIL"
        print(f"  [{status}] {gate['name']}")
        for k, v in gate.items():
            if k in ("name", "passed"):
                continue
            print(f"         {k}: {v}")
    print("-" * 78)
    print(f"  VERDICT: {kill_switch['verdict']}")
    print("=" * 78)

    if args.visual_prompt_stretch:
        print("")
        print("=" * 78)
        print("VISUAL-PROMPT STRETCH (item_in_slot only, RepRTA path)")
        print("=" * 78)
        for k, v in visual_prompt_report.items():
            print(f"  {k:<22} {v}")
        print("=" * 78)

    # Exit non-zero on kill-switch failure so CI / calling scripts can
    # notice. Script still writes the full report first — failure is a
    # signal, not a crash.
    return 0 if kill_switch["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
