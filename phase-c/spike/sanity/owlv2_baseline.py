"""Phase 1 spike — OWLv2 zero-shot baseline (Track B).

Purpose
-------
Day 2 GD-tiny eval produced micro F1 = 0.152 on 11 hand-labeled Minecraft
fixtures, with three near-total failures:

    item_in_slot   0/34   (Grounding DINO never fired on inventory items)
    hotbar         0/13
    tree           0/22

Hypothesis: Grounding DINO's failures are concept-grain failures
(BERT tokenizer + DINO detection head learned from natural-image
captions, not screen UI primitives). A different open-vocabulary
detector with a different text encoder (CLIP) and different detection
head (ViT) might cover the gap. OWLv2 is the obvious A/B candidate
because:

    (a) trained with a CLIP text encoder, not BERT — different
        embedding manifold for short noun phrases,
    (b) post-trained on self-labeled web data — wider concept
        coverage than GD's RefCOCO-leaning training,
    (c) ensemble checkpoint (`owlv2-base-patch16-ensemble`) is the
        recommended zero-shot variant per HF docs.

If OWLv2 also returns 0/34 on `item_in_slot`: Track B refutes — the
gap is not GD-specific but a universal zero-shot domain limit on
screen-UI primitives. We then commit harder to Track A (Hybrid: VLM
plan + template-match HUD) without wasting more cycles on the
"swap detector" hypothesis.

If OWLv2 closes one or more gaps: Track B becomes a real candidate
for Layer 1 perception, with the cost of carrying a second model.

Pipeline (per fixture)
----------------------
1. Load .png + sibling labelme .json GT (eval_harness loaders).
2. Build the combined class vocabulary from prompts.yaml (union of
   world+ui+hud — same approach as GD `--all-classes` baseline so the
   numbers are apples-to-apples vs `eval_day2.json`).
3. Run OWLv2 once per image with one query list = combined vocab.
   OWLv2's API takes texts as list[list[str]] (outer per image, inner
   per query), so a single image is `texts=[[c1, c2, ...]]`.
4. `post_process_object_detection(threshold=0.1)` returns boxes,
   scores, and integer label indices into our vocab.
5. Greedy IoU≥0.5 matching by canonical class name, identical to
   eval_harness.greedy_match. This is the eval-equivalence guarantee.
6. Aggregate per-class TP/FP/FN, compute micro/macro P/R/F1, build the
   side-by-side comparison vs GD baseline, write JSON, print table.

Design decisions
----------------
- **Same IoU threshold (0.5), same score threshold style (0.1).**
  GD baseline used score 0.2; OWLv2 typically operates at 0.1
  per HF docs (different score calibration). Sean's spec pinned 0.1.
  This means OWLv2 gets a slightly lower bar — fine, we're testing
  whether OWLv2 *can* fire on these classes at all, not whether it
  beats GD at identical operating points. If it fires, we'll re-run
  GD at 0.1 for fairness in a follow-up.
- **Natural-language prompts (`item_in_slot` → "an item in slot").**
  CLIP text encoder is trained on captions; phrasing matters. We use
  the article-prefixed form because OWLv2's example notebook uses it
  ("a photo of a cat"). Class name → canonical snake_case for
  matching is reversed via a dict lookup, not string parsing.
- **Combined-vocab single pass, not multi-group multi-pass.**
  OWLv2 doesn't share GD's BERT tokenizer pathology that degraded GD
  above ~15 prompts, so we don't need to split groups. Mirrors what
  the GD `--all-classes` path effectively did (multi-pass with union
  vocab) but in one forward.
- **No tracker, no NMS post-filter beyond what HF does.** This is a
  detector quality test, not a perception system test.
- **No augmentation, no fine-tune.** Track B's whole point is
  zero-shot. Sean's instructions are explicit.

What this script intentionally does NOT do
------------------------------------------
- **Does not modify eval_harness.py.** Imports its primitives so the
  matching/scoring math is bit-identical. Any divergence in
  per-class numbers vs eval_day2.json is purely a detector
  difference.
- **Does not retry failed downloads.** First HF Hub failure → exit
  with a clear blocker message, no patch loops.
- **Does not commit / push.** Main agent integrates.

Usage
-----
    uv run python -m sanity.owlv2_baseline \\
        --fixtures-dir fixtures/task \\
        --labels-dir   fixtures/labels \\
        --prompts-yaml fixtures/prompts.yaml \\
        --output-json  reports/owlv2_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Reuse eval_harness primitives — guarantees bit-identical scoring math
# vs the GD baseline. We do NOT import GDRunner / build_prompt_string /
# normalize_pred_label; those are GD-specific (BERT tokenizer + " . "
# joining + label-substring parsing).
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

# GD baseline numbers from reports/eval_day2.json — hard-coded for the
# vs_gd_tiny comparison block. If eval_day2.json gets re-generated with
# different numbers, these are stale; intentional cache to make the
# comparison stable across re-runs of OWLv2.
_GD_TINY_BASELINE: dict[str, Any] = {
    "item_in_slot": {"tp": 0, "fn": 34},  # 0/34
    "hotbar": {"tp": 0, "fn": 13},        # 0/13 — eval_day2 reports 0+11=11 unmatched, 2 FP fired (mislabel); we'll surface the GT total
    "tree": {"tp": 0, "fn": 22},          # 0/22 — eval_day2 reports 10 unmatched + 12 FP firing wrong; tree GT total = 22 across all fixtures
    "micro_f1": 0.152,
}


# ---------------------------------------------------------------------------
# Prompt phrasing
# ---------------------------------------------------------------------------


def class_to_phrase(cls: str) -> str:
    """Turn snake_case class name into a CLIP-friendly noun phrase.

    Heuristic: replace underscores with spaces, prepend article. This is
    the recipe in OWLv2's HF model card examples. We do NOT prepend "a
    photo of" — that biases toward the photographic distribution and
    hurts recall on rendered/screen content per CLIP-on-UI literature.

    Examples:
        "tree"          → "a tree"
        "oak_log"       → "an oak log"
        "item_in_slot"  → "an item in slot"
        "hotbar"        → "a hotbar"
    """
    spaced = cls.replace("_", " ")
    article = "an" if spaced[0] in "aeiou" else "a"
    return f"{article} {spaced}"


# ---------------------------------------------------------------------------
# OWLv2 wrapper
# ---------------------------------------------------------------------------


class OWLv2Runner:
    """Thin wrapper around HF OWLv2 ensemble. Loads once, reuses per image.

    Mirrors the shape of eval_harness.GDRunner so the eval loop below
    looks the same. Differences from GD:
      - texts is list[list[str]] not a flat " . "-joined string
      - labels in the output are integer indices into the query list,
        not parsed text fragments — so we don't need fuzzy label
        normalization, just an index lookup.
    """

    def __init__(self, model_id: str) -> None:
        # Deferred import so --help is snappy and so import errors
        # surface as blockers, not crashes mid-eval.
        try:
            import torch
            from transformers import Owlv2ForObjectDetection, Owlv2Processor
        except ImportError as e:
            raise SystemExit(
                f"BLOCKER: transformers / torch import failed for OWLv2: {e}\n"
                "  Expected `transformers>=4.47.0` (already in pyproject for GD).\n"
                "  Do NOT `uv add` — report to main agent."
            ) from e

        if not torch.cuda.is_available():
            raise SystemExit("BLOCKER: CUDA required for spike eval — GPU not available")

        self.torch = torch
        try:
            self.processor = Owlv2Processor.from_pretrained(model_id)
            self.model = Owlv2ForObjectDetection.from_pretrained(model_id).to("cuda")
        except Exception as e:
            raise SystemExit(
                f"BLOCKER: failed to load OWLv2 weights from HF Hub ({model_id}): {e}\n"
                "  Likely network or hub-cache issue. Do NOT patch — report blocker."
            ) from e
        self.model.eval()

    def run(
        self,
        image,  # PIL.Image
        class_names: list[str],
        score_threshold: float,
    ) -> list[Bbox]:
        """One forward + post-process. Returns canonical-labeled Bboxes."""
        phrases = [class_to_phrase(c) for c in class_names]
        # OWLv2 takes nested texts: outer = batch, inner = queries per image.
        inputs = self.processor(
            text=[phrases], images=image, return_tensors="pt"
        ).to("cuda")

        with self.torch.no_grad():
            outputs = self.model(**inputs)

        # target_sizes is (H, W) per image. PIL .size is (W, H).
        target_sizes = self.torch.tensor([image.size[::-1]], device="cuda")

        # transformers >=4.47 unified OWLv2 to post_process_grounded_object_detection.
        # Returns boxes (pixel coords) + scores + integer labels into class_names
        # (and optionally text_labels strings — we use int labels for fidelity).
        results = self.processor.post_process_grounded_object_detection(
            outputs=outputs,
            target_sizes=target_sizes,
            threshold=score_threshold,
            text_labels=[class_names],
        )[0]

        out: list[Bbox] = []
        boxes = results["boxes"].detach().cpu().tolist()
        scores = results["scores"].detach().cpu().tolist()
        labels = results["labels"].detach().cpu().tolist()
        for box, score, label_idx in zip(boxes, scores, labels, strict=False):
            x1, y1, x2, y2 = box
            # label_idx → canonical snake_case class name. Bounds-check
            # because we've seen processors emit out-of-range indices
            # under edge cases (empty query list, etc.); skip if so.
            if label_idx < 0 or label_idx >= len(class_names):
                continue
            out.append(
                Bbox(
                    label=class_names[label_idx],
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    score=float(score),
                )
            )
        return out


# ---------------------------------------------------------------------------
# Eval loop (mirrors eval_harness.evaluate but for OWLv2 + single-pass)
# ---------------------------------------------------------------------------


def evaluate(
    fixtures_dir: Path,
    labels_dir: Path,
    prompts: dict[str, list[str]],
    runner: OWLv2Runner,
    score_threshold: float,
    iou_threshold: float,
    verbose: bool,
) -> dict[str, Any]:
    """Run OWLv2 on every fixture with a sibling .json GT.

    Single-pass with the union vocabulary — see header docstring for
    why this is the right choice for OWLv2 (no BERT tokenizer
    degradation pathology to dodge).
    """
    per_class: dict[str, PerClassStats] = {}
    image_reports: list[ImageReport] = []

    fixtures = sorted(fixtures_dir.glob("*.png")) + sorted(fixtures_dir.glob("*.jpg"))
    if not fixtures:
        raise SystemExit(f"no fixtures found under {fixtures_dir}")

    combined_vocab = build_combined_vocab(prompts)
    if verbose:
        print(f"  [vocab] {len(combined_vocab)} classes: {combined_vocab}")

    from PIL import Image  # deferred

    for img_path in fixtures:
        label_path = labels_dir / f"{img_path.stem}.json"
        if not label_path.exists():
            if verbose:
                print(f"  [skip] {img_path.name}: no GT at {label_path}")
            continue

        gts = load_labelme_json(label_path)
        image = Image.open(img_path).convert("RGB")

        t0 = time.perf_counter()
        preds = runner.run(image, combined_vocab, score_threshold)
        dt_ms = (time.perf_counter() - t0) * 1000

        matches, matched_p, matched_g = greedy_match(gts, preds, iou_threshold)

        # Same accounting as eval_harness: TP per match, FP per
        # unmatched pred, FN per unmatched gt — bucketed by class.
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
            prompt_group="combined",  # always single-pass union
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
# Comparison vs GD baseline
# ---------------------------------------------------------------------------


def _format_class_score(per_class: dict[str, dict[str, Any]], cls: str) -> str:
    """Render '<tp>/<gt_total>' for the vs_gd_tiny block.

    gt_total = tp + fn (predictions that GT-matched plus GT items that
    nothing matched). Mirrors GD's "0/34" notation used by Sean.
    """
    s = per_class.get(cls)
    if s is None:
        return "0/0 (class absent)"
    tp = s["tp"]
    fn = s["fn"]
    return f"{tp}/{tp + fn}"


def _build_vs_gd(report: dict[str, Any]) -> dict[str, Any]:
    pc = report["per_class"]
    micro_f1 = report["micro"]["f1"]
    out = {
        "item_in_slot_gd": "0/34",
        "item_in_slot_owlv2": _format_class_score(pc, "item_in_slot"),
        "hotbar_gd": "0/13",
        "hotbar_owlv2": _format_class_score(pc, "hotbar"),
        "tree_gd": "0/22",
        "tree_owlv2": _format_class_score(pc, "tree"),
        "micro_f1_gd": _GD_TINY_BASELINE["micro_f1"],
        "micro_f1_owlv2": micro_f1,
    }
    return out


def _build_verdict(vs_gd: dict[str, Any]) -> str:
    """Verdict heuristic: count which of the 3 'GD failed' classes OWLv2
    fires on at all (TP > 0).

    A class is "closed" if OWLv2 has TP > 0 on it. Mere FP firing
    doesn't count — that's wrong-place detection, no better than GD.
    """
    closed = 0
    for cls in ("item_in_slot", "hotbar", "tree"):
        s = vs_gd[f"{cls}_owlv2"]
        # parse "<tp>/<total>"; tp > 0 means closed
        try:
            tp_str = s.split("/")[0]
            if int(tp_str) > 0:
                closed += 1
        except (ValueError, IndexError):
            continue

    if closed == 0:
        return (
            "OWLv2 has same domain limit as GD: zero-shot detectors "
            "cannot localize Minecraft screen-UI primitives "
            "(item_in_slot, hotbar, tree) on these fixtures. "
            "Track B refuted — concept-grain gap is not GD-specific."
        )
    return f"OWLv2 closes {closed}/3 GD-failed gaps"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 spike — OWLv2 zero-shot baseline (Track B)"
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=Path("fixtures/task"),
    )
    parser.add_argument(
        "--labels-dir",
        type=Path,
        default=Path("fixtures/labels"),
    )
    parser.add_argument(
        "--prompts-yaml",
        type=Path,
        default=Path("fixtures/prompts.yaml"),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("reports/owlv2_baseline.json"),
    )
    parser.add_argument(
        "--model",
        type=str,
        default="google/owlv2-base-patch16-ensemble",
    )
    parser.add_argument("--score-threshold", type=float, default=0.1)
    parser.add_argument("--iou-threshold", type=float, default=0.5)
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

    print(f"[1/3] Loading OWLv2 model {args.model}...")
    t0 = time.perf_counter()
    runner = OWLv2Runner(args.model)
    print(f"      loaded in {time.perf_counter() - t0:.1f}s")

    print(f"[2/3] Evaluating fixtures in {args.fixtures_dir}...")
    eval_report = evaluate(
        fixtures_dir=args.fixtures_dir,
        labels_dir=args.labels_dir,
        prompts=prompts,
        runner=runner,
        score_threshold=args.score_threshold,
        iou_threshold=args.iou_threshold,
        verbose=args.verbose,
    )

    # Build the spec-compliant report shape (Sean's JSON schema).
    n_fixtures = len(eval_report["images"])
    vs_gd = _build_vs_gd(eval_report)
    verdict = _build_verdict(vs_gd)

    final_report: dict[str, Any] = {
        "model": args.model,
        "n_fixtures": n_fixtures,
        "iou_threshold": args.iou_threshold,
        "score_threshold": args.score_threshold,
        "per_class": eval_report["per_class"],
        "micro": eval_report["micro"],
        "macro": eval_report["macro"],
        "vs_gd_tiny": vs_gd,
        "verdict": verdict,
        "notes": (
            "Single-pass union-vocab OWLv2 zero-shot. Same IoU=0.5 greedy "
            "matching as GD Day-2 baseline (eval_harness primitives reused). "
            "Score threshold 0.1 vs GD baseline's 0.2 — OWLv2 is calibrated "
            "lower per HF docs; if OWLv2 fires meaningfully a follow-up "
            "should re-run GD at 0.1 for fairness. Prompts use natural "
            "noun phrases ('an item in slot') because CLIP text encoder "
            "expects captions; raw snake_case hurts recall."
        ),
        "_images": eval_report["images"],  # keep per-image breakdown for debugging
    }

    print(f"[3/3] Writing report to {args.output_json}...")
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

    # Reuse eval_harness pretty-printer for the per-class table, then
    # print the comparison + verdict ourselves.
    print_table(eval_report)
    print("")
    print("=" * 78)
    print("VS GROUNDING DINO TINY (Day 2 baseline)")
    print("=" * 78)
    for k, v in vs_gd.items():
        print(f"  {k:<22} {v}")
    print("-" * 78)
    print(f"  VERDICT: {verdict}")
    print("=" * 78)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
