"""Phase 1 spike — Grounding DINO evaluation harness.

Day 2 gate: run Grounding DINO against a hand-labeled fixture set and
compute per-class precision / recall / F1 / mean IoU at a configurable IoU
threshold (default 0.5). Output is a JSON report plus a human-readable
table to stdout.

Pipeline (per fixture)
----------------------
1. Load the .png and the sibling labelme .json ground truth.
2. Pick the prompt group from ``prompts.yaml`` based on which classes
   appear in the GT (UI group if any UI label present, else HUD if any
   HUD label, else world group). This keeps the GD text prompt short —
   GD degrades quality above ~10-15 classes per prompt.
3. Run one forward pass + ``post_process_grounded_object_detection`` at
   the given box/text thresholds. Transformers 5.x renamed
   ``box_threshold`` → ``threshold``; we pass both names nowhere and use
   only the 5.x signature.
4. For each GT box, find the best-IoU predicted box with matching label.
   Greedy one-to-one matching with IoU ≥ ``--iou-threshold`` counts as
   TP. Remaining GT → FN. Remaining predictions whose label is in the
   prompt → FP. Predictions whose label parsed back to something not in
   the prompt are ignored (GD occasionally emits empty / partial tokens).

Aggregation
-----------
Per-class: TP, FP, FN, precision = TP/(TP+FP), recall = TP/(TP+FN),
F1 = 2PR/(P+R), and mean IoU over that class's TP matches only (FP/FN
contribute nothing — mean IoU on TP measures localization quality given
that the detector fired on the right thing).

Overall: micro-averaged (sum TP/FP/FN across classes, then the ratios)
*and* macro (unweighted mean of per-class metrics, skipping NaN classes).

Design decision — greedy not Hungarian
--------------------------------------
Standard detection mAP uses greedy matching sorted by prediction score.
We do the same. Hungarian (optimal assignment) would marginally improve
the metric when the detector fires multiple overlapping boxes on the
same GT, but it's gratuitous for a spike — the difference is < 1pt F1
on dense scenes and zero on sparse ones. Keep it simple, match the
literature.

Edge cases intentionally NOT handled
------------------------------------
- **No crowd / ignore regions.** If the annotator missed labeling a
  visible tree, GD detections on it count as FP. Annotator's job.
- **Multi-instance same label on same GT.** If 3 predictions all overlap
  one GT tree, 1 is TP, 2 are FP. Correct per COCO convention.
- **Class-name fuzzy matching.** GD occasionally returns "tree log"
  when prompted "oak_log". We do exact-match only. Logged in verbose.

Usage
-----
    python -m sanity.eval_harness \\
        --fixtures-dir fixtures/task \\
        --labels-dir   fixtures/labels \\
        --prompts-yaml fixtures/prompts.yaml \\
        --output-json  reports/eval_day2.json \\
        --threshold 0.3 --iou-threshold 0.5 --verbose
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keys we recognize in prompts.yaml. Order matters for group selection:
# UI wins over HUD wins over world if a fixture has mixed labels (rare).
_GROUP_ORDER = ("ui", "hud", "world")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Bbox:
    """Axis-aligned box in pixel coords, (x1,y1) top-left, (x2,y2) bot-right."""

    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 1.0  # 1.0 for GT, <=1.0 for predictions

    @classmethod
    def from_labelme_points(cls, label: str, points: list[list[float]]) -> "Bbox":
        """Build from labelme's 2-point rectangle shape, normalizing order."""
        (ax, ay), (bx, by) = points[0], points[1]
        return cls(
            label=label,
            x1=float(min(ax, bx)),
            y1=float(min(ay, by)),
            x2=float(max(ax, bx)),
            y2=float(max(ay, by)),
        )

    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


@dataclass
class PerClassStats:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    iou_sum: float = 0.0  # summed IoU across TP matches only

    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else float("nan")

    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else float("nan")

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        if math.isnan(p) or math.isnan(r) or (p + r) == 0:
            return float("nan")
        return 2 * p * r / (p + r)

    def mean_iou(self) -> float:
        return self.iou_sum / self.tp if self.tp else float("nan")


@dataclass
class ImageReport:
    image: str
    prompt_group: str
    n_gt: int
    n_pred: int
    tp: int
    fp: int
    fn: int
    matches: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# IoU + matching
# ---------------------------------------------------------------------------


def iou(a: Bbox, b: Bbox) -> float:
    """Standard axis-aligned box IoU. Returns 0.0 on empty intersection.

    Guards against degenerate (zero-area) boxes that can appear when a
    label was drawn as a single click in labelme — returns 0.0 rather
    than dividing by zero.
    """
    inter_x1 = max(a.x1, b.x1)
    inter_y1 = max(a.y1, b.y1)
    inter_x2 = min(a.x2, b.x2)
    inter_y2 = min(a.y2, b.y2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter = inter_w * inter_h
    union = a.area() + b.area() - inter
    if union <= 0:
        return 0.0
    return inter / union


def greedy_match(
    gts: list[Bbox],
    preds: list[Bbox],
    iou_threshold: float,
) -> tuple[list[tuple[int, int, float]], set[int], set[int]]:
    """Greedy one-to-one matching.

    Predictions sorted by score desc. Each prediction grabs the GT of the
    same label with highest IoU ≥ threshold that hasn't been taken yet.

    Returns
    -------
    matches : list of (pred_idx, gt_idx, iou) for TP pairs
    matched_preds : set of pred indices that matched (complement = FP)
    matched_gts   : set of gt indices that matched (complement = FN)
    """
    pred_order = sorted(range(len(preds)), key=lambda i: -preds[i].score)
    matched_preds: set[int] = set()
    matched_gts: set[int] = set()
    matches: list[tuple[int, int, float]] = []

    for pi in pred_order:
        p = preds[pi]
        best_iou = iou_threshold
        best_gt = -1
        for gi, g in enumerate(gts):
            if gi in matched_gts:
                continue
            if g.label != p.label:
                continue
            i = iou(p, g)
            if i >= best_iou:
                best_iou = i
                best_gt = gi
        if best_gt >= 0:
            matched_preds.add(pi)
            matched_gts.add(best_gt)
            matches.append((pi, best_gt, best_iou))

    return matches, matched_preds, matched_gts


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def load_prompts_yaml(path: Path) -> dict[str, list[str]]:
    """Load prompts.yaml. Pulls pyyaml from the env; fails clean if missing."""
    try:
        import yaml
    except ImportError as e:
        raise SystemExit(
            "pyyaml not installed — `uv pip install pyyaml` in the spike venv."
        ) from e
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping of group → list")
    out: dict[str, list[str]] = {}
    for group, classes in data.items():
        if not isinstance(classes, list):
            raise ValueError(f"{path}: group {group!r} must be a list")
        out[group] = [str(c).strip() for c in classes if str(c).strip()]
    return out


def load_labelme_json(path: Path) -> list[Bbox]:
    """Parse labelme JSON → list[Bbox]. Silently skips non-rectangle shapes."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    out: list[Bbox] = []
    for shape in data.get("shapes", []):
        if shape.get("shape_type") != "rectangle":
            continue
        pts = shape.get("points") or []
        if len(pts) != 2:
            continue
        out.append(Bbox.from_labelme_points(shape["label"], pts))
    return out


def pick_prompt_group(
    gt_labels: set[str],
    groups: dict[str, list[str]],
) -> str:
    """Decide which group's prompt string to use for this fixture.

    Rule: first group in _GROUP_ORDER that shares at least one class with
    the GT. Fallback = first defined group. This lets a fixture be a UI
    screenshot (picks `ui`), a HUD-heavy frame (picks `hud`), or a pure
    world frame (picks `world`).
    """
    for g in _GROUP_ORDER:
        if g not in groups:
            continue
        if gt_labels & set(groups[g]):
            return g
    # fallback — no GT labels matched any group, pick first defined
    for g in _GROUP_ORDER:
        if g in groups:
            return g
    raise ValueError("no prompt groups defined")


# ---------------------------------------------------------------------------
# Model wrapper
# ---------------------------------------------------------------------------


class GDRunner:
    """Thin wrapper around HF Grounding DINO, loads once, reuses per image."""

    def __init__(self, model_id: str) -> None:
        # Deferred import so --help is snappy.
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        if not torch.cuda.is_available():
            raise SystemExit("CUDA required for spike eval — GPU not available")
        self.torch = torch
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(model_id).to(
            "cuda"
        )
        self.model.eval()

    def run(
        self,
        image,  # PIL.Image
        prompt: str,
        threshold: float,
        text_threshold: float,
    ) -> list[Bbox]:
        """One forward + post-process. Returns list[Bbox] with scores."""
        inputs = self.processor(images=image, text=prompt, return_tensors="pt").to(
            "cuda"
        )
        with self.torch.no_grad():
            outputs = self.model(**inputs)
        # transformers 5.x: `threshold`, not `box_threshold`.
        results = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=threshold,
            text_threshold=text_threshold,
            target_sizes=[image.size[::-1]],  # (H, W)
        )[0]
        out: list[Bbox] = []
        for box, score, label in zip(
            results["boxes"], results["scores"], results["labels"], strict=False
        ):
            x1, y1, x2, y2 = box.tolist()
            out.append(
                Bbox(
                    label=str(label).strip().replace(" ", "_"),
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    score=float(score),
                )
            )
        return out


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def build_prompt_string(classes: list[str]) -> str:
    """GD expects classes joined by ' . '."""
    return " . ".join(classes)


def normalize_pred_label(pred_label: str, prompt_classes: list[str]) -> str | None:
    """Map a GD-returned label back to a prompt class.

    GD emits whatever substring its text encoder matched, which usually
    equals one of the prompt classes but occasionally has trailing punct
    or is empty. We exact-match first; if that fails, try stripped and
    lowercase. Unmatchable labels → None (those predictions are dropped,
    not counted as FP, because we can't even identify what class they're
    claiming to be).
    """
    candidates = {c: c for c in prompt_classes}
    if pred_label in candidates:
        return candidates[pred_label]
    cleaned = pred_label.strip().lower().replace(" ", "_")
    if cleaned in candidates:
        return candidates[cleaned]
    return None


def evaluate(
    fixtures_dir: Path,
    labels_dir: Path,
    prompts: dict[str, list[str]],
    runner: GDRunner,
    threshold: float,
    text_threshold: float,
    iou_threshold: float,
    allow_no_gt: bool,
    verbose: bool,
) -> dict[str, Any]:
    """Iterate all .png fixtures, run GD, accumulate per-class stats.

    If a fixture has no sibling .json GT:
      - ``allow_no_gt=True``  : run GD, all predictions counted as FP
      - ``allow_no_gt=False`` : skip the fixture
    """
    per_class: dict[str, PerClassStats] = {}
    image_reports: list[ImageReport] = []

    fixtures = sorted(fixtures_dir.glob("*.png")) + sorted(fixtures_dir.glob("*.jpg"))
    if not fixtures:
        raise SystemExit(f"no fixtures found under {fixtures_dir}")

    from PIL import Image  # deferred

    for img_path in fixtures:
        label_path = labels_dir / f"{img_path.stem}.json"
        has_gt = label_path.exists()

        if not has_gt and not allow_no_gt:
            if verbose:
                print(f"  [skip] {img_path.name}: no GT at {label_path}")
            continue

        gts = load_labelme_json(label_path) if has_gt else []
        gt_labels = {g.label for g in gts}
        group = pick_prompt_group(gt_labels, prompts)
        classes = prompts[group]
        prompt_str = build_prompt_string(classes)

        image = Image.open(img_path).convert("RGB")
        t0 = time.perf_counter()
        raw_preds = runner.run(image, prompt_str, threshold, text_threshold)
        dt_ms = (time.perf_counter() - t0) * 1000

        # Re-map pred labels into prompt-class vocabulary, drop untranslatable.
        preds: list[Bbox] = []
        dropped = 0
        for p in raw_preds:
            canon = normalize_pred_label(p.label, classes)
            if canon is None:
                dropped += 1
                continue
            preds.append(
                Bbox(
                    label=canon,
                    x1=p.x1,
                    y1=p.y1,
                    x2=p.x2,
                    y2=p.y2,
                    score=p.score,
                )
            )

        matches, matched_p, matched_g = greedy_match(gts, preds, iou_threshold)

        # Accumulate: TP per (pred, gt) pair; FP per unmatched pred;
        # FN per unmatched gt. Bucket all three by class. For TP the class
        # of prediction and GT are guaranteed equal by greedy_match.
        for pi, gi, i_iou in matches:
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
            prompt_group=group,
            n_gt=len(gts),
            n_pred=len(preds),
            tp=len(matches),
            fp=len(preds) - len(matched_p),
            fn=len(gts) - len(matched_g),
        )
        if verbose:
            for pi, gi, i_iou in matches:
                rep.matches.append(
                    {
                        "type": "TP",
                        "label": preds[pi].label,
                        "score": round(preds[pi].score, 3),
                        "iou": round(i_iou, 3),
                    }
                )
            for pi, p in enumerate(preds):
                if pi not in matched_p:
                    rep.matches.append(
                        {
                            "type": "FP",
                            "label": p.label,
                            "score": round(p.score, 3),
                        }
                    )
            for gi, g in enumerate(gts):
                if gi not in matched_g:
                    rep.matches.append({"type": "FN", "label": g.label})
            print(
                f"  [{img_path.name}] group={group} "
                f"gt={rep.n_gt} pred={rep.n_pred} "
                f"TP={rep.tp} FP={rep.fp} FN={rep.fn} "
                f"dropped_preds={dropped} t={dt_ms:.0f}ms"
            )
            for m in rep.matches:
                print(f"      {m}")
        image_reports.append(rep)

    return _finalize(per_class, image_reports, iou_threshold, threshold)


def _finalize(
    per_class: dict[str, PerClassStats],
    image_reports: list[ImageReport],
    iou_threshold: float,
    threshold: float,
) -> dict[str, Any]:
    """Build the report dict: per-class, micro, macro, and image detail."""
    classes_out: dict[str, dict[str, float]] = {}
    tp_sum = fp_sum = fn_sum = 0
    iou_sum = 0.0
    macro_p: list[float] = []
    macro_r: list[float] = []
    macro_f: list[float] = []
    macro_i: list[float] = []

    for cls in sorted(per_class):
        s = per_class[cls]
        tp_sum += s.tp
        fp_sum += s.fp
        fn_sum += s.fn
        iou_sum += s.iou_sum
        p, r, f, mi = s.precision(), s.recall(), s.f1(), s.mean_iou()
        if not math.isnan(p):
            macro_p.append(p)
        if not math.isnan(r):
            macro_r.append(r)
        if not math.isnan(f):
            macro_f.append(f)
        if not math.isnan(mi):
            macro_i.append(mi)
        classes_out[cls] = {
            "tp": s.tp,
            "fp": s.fp,
            "fn": s.fn,
            "precision": _round_or_none(p),
            "recall": _round_or_none(r),
            "f1": _round_or_none(f),
            "mean_iou": _round_or_none(mi),
        }

    micro_p = tp_sum / (tp_sum + fp_sum) if (tp_sum + fp_sum) else float("nan")
    micro_r = tp_sum / (tp_sum + fn_sum) if (tp_sum + fn_sum) else float("nan")
    micro_f = (
        2 * micro_p * micro_r / (micro_p + micro_r)
        if (not math.isnan(micro_p) and not math.isnan(micro_r) and (micro_p + micro_r))
        else float("nan")
    )
    micro_iou = iou_sum / tp_sum if tp_sum else float("nan")

    return {
        "config": {
            "score_threshold": threshold,
            "iou_threshold": iou_threshold,
        },
        "per_class": classes_out,
        "micro": {
            "tp": tp_sum,
            "fp": fp_sum,
            "fn": fn_sum,
            "precision": _round_or_none(micro_p),
            "recall": _round_or_none(micro_r),
            "f1": _round_or_none(micro_f),
            "mean_iou": _round_or_none(micro_iou),
        },
        "macro": {
            "precision": _round_or_none(_mean(macro_p)),
            "recall": _round_or_none(_mean(macro_r)),
            "f1": _round_or_none(_mean(macro_f)),
            "mean_iou": _round_or_none(_mean(macro_i)),
            "n_classes_with_support": len(
                [c for c, s in per_class.items() if (s.tp + s.fn) > 0]
            ),
        },
        "images": [ir.__dict__ for ir in image_reports],
    }


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _round_or_none(x: float) -> float | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), 4)


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------


def print_table(report: dict[str, Any]) -> None:
    print("")
    print("=" * 78)
    print(
        f"EVAL RESULTS  (score_threshold={report['config']['score_threshold']}, "
        f"iou_threshold={report['config']['iou_threshold']})"
    )
    print("=" * 78)
    header = f"{'class':<22} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>7} {'R':>7} {'F1':>7} {'mIoU':>7}"
    print(header)
    print("-" * 78)
    for cls, s in report["per_class"].items():
        print(
            f"{cls:<22} {s['tp']:>4} {s['fp']:>4} {s['fn']:>4} "
            f"{_fmt(s['precision']):>7} {_fmt(s['recall']):>7} "
            f"{_fmt(s['f1']):>7} {_fmt(s['mean_iou']):>7}"
        )
    print("-" * 78)
    mi = report["micro"]
    ma = report["macro"]
    print(
        f"{'MICRO':<22} {mi['tp']:>4} {mi['fp']:>4} {mi['fn']:>4} "
        f"{_fmt(mi['precision']):>7} {_fmt(mi['recall']):>7} "
        f"{_fmt(mi['f1']):>7} {_fmt(mi['mean_iou']):>7}"
    )
    print(
        f"{'MACRO':<22} {'':>4} {'':>4} {'':>4} "
        f"{_fmt(ma['precision']):>7} {_fmt(ma['recall']):>7} "
        f"{_fmt(ma['f1']):>7} {_fmt(ma['mean_iou']):>7}   "
        f"(n_classes_with_support={ma['n_classes_with_support']})"
    )
    print("=" * 78)


def _fmt(x: float | None) -> str:
    return "  NaN" if x is None else f"{x:.3f}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 spike — GD per-class P/R/F1/mIoU eval harness"
    )
    parser.add_argument("--fixtures-dir", type=Path, required=True)
    parser.add_argument("--labels-dir", type=Path, required=True)
    parser.add_argument("--prompts-yaml", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument(
        "--model",
        type=str,
        default="IDEA-Research/grounding-dino-tiny",
    )
    parser.add_argument("--threshold", type=float, default=0.3)
    parser.add_argument("--text-threshold", type=float, default=0.25)
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument(
        "--allow-no-gt",
        action="store_true",
        help="Run GD on fixtures without GT (all preds → FP). Default skips.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    for p in (args.fixtures_dir, args.labels_dir, args.prompts_yaml):
        if not p.exists():
            print(f"ERROR: path not found: {p}", file=sys.stderr)
            return 2

    prompts = load_prompts_yaml(args.prompts_yaml)
    if not prompts:
        print(f"ERROR: no groups in {args.prompts_yaml}", file=sys.stderr)
        return 2

    print(f"[1/3] Loading model {args.model}...")
    t0 = time.perf_counter()
    runner = GDRunner(args.model)
    print(f"      loaded in {time.perf_counter() - t0:.1f}s")

    print(f"[2/3] Evaluating fixtures in {args.fixtures_dir}...")
    report = evaluate(
        fixtures_dir=args.fixtures_dir,
        labels_dir=args.labels_dir,
        prompts=prompts,
        runner=runner,
        threshold=args.threshold,
        text_threshold=args.text_threshold,
        iou_threshold=args.iou_threshold,
        allow_no_gt=args.allow_no_gt,
        verbose=args.verbose,
    )

    print(f"[3/3] Writing report to {args.output_json}...")
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
