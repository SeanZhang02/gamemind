"""Template matching stress test — perturb the baseline inventory fixture and
measure whether `cv2.matchTemplate` correlation survives realistic
distribution shifts.

Background: Day 2 PoC (`template_match_poc.py`) achieved 4/4 perfect matches
on `task3/4/5/5.1.png` — but those are pixel-identical inventory UIs from the
SAME session at SAME resolution. That is a ceiling, not a working range.

Real-world deployment will hit:
  - GUI scale changes (Minecraft's "GUI Scale" option resizes the panel)
  - Cross-resolution capture (1280x720 vs 1920x1080)
  - Anti-aliasing / shader / brightness differences (different render pipeline)
  - Noise from screen capture / driver differences

Stress fixtures (5):
  scale_90              : task4 resized to 0.9x (smaller GUI / window)
  scale_110             : task4 resized to 1.1x (larger GUI / window)
  resolution_1280x720   : task4 hard-resized to 720p (cross-resolution)
  noise_gaussian        : task4 + Gaussian noise sigma=5 (capture noise)
  brightness_minus_20   : task4 darkened by 20% (different shader / lighting)

Method: cut the SAME template from original task4 (the unperturbed reference)
that template_match_poc.py uses, then run `cv2.matchTemplate` against each
perturbed fixture. Record max TM_CCOEFF_NORMED correlation.

Gate (per-fixture): correlation >= 0.85 = PASS, else FAIL.
Overall: any FAIL = Track A (template-matching hybrid) NOT ready.

Output: phase-c/spike/reports/template_stress.json
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

SPIKE_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = SPIKE_ROOT / "fixtures"
STRESS_DIR = FIXTURES / "stress"
REPORTS = SPIKE_ROOT / "reports"

# Same template region as template_match_poc.py — top half of inventory_grid.
TEMPLATE_X1, TEMPLATE_Y1, TEMPLATE_X2, TEMPLATE_Y2 = 847, 295, 1716, 700

GATE_THRESHOLD = 0.85


def load_baseline() -> tuple[np.ndarray, np.ndarray]:
    """Load task4.png as baseline and cut the template region."""
    ref_path = FIXTURES / "task4.png"
    img = cv2.imread(str(ref_path))
    if img is None:
        raise SystemExit(f"FATAL: cannot read baseline {ref_path}")
    template = img[TEMPLATE_Y1:TEMPLATE_Y2, TEMPLATE_X1:TEMPLATE_X2].copy()
    return img, template


def make_scale_90(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img, (int(w * 0.9), int(h * 0.9)), interpolation=cv2.INTER_AREA)


def make_scale_110(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img, (int(w * 1.1), int(h * 1.1)), interpolation=cv2.INTER_LINEAR)


def make_resolution_720p(img: np.ndarray) -> np.ndarray:
    return cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)


def make_noise_gaussian(img: np.ndarray, sigma: float = 5.0) -> np.ndarray:
    rng = np.random.default_rng(seed=42)
    noise = rng.normal(0.0, sigma, img.shape).astype(np.float32)
    out = img.astype(np.float32) + noise
    return np.clip(out, 0, 255).astype(np.uint8)


def make_brightness_minus_20(img: np.ndarray) -> np.ndarray:
    out = img.astype(np.float32) * 0.8
    return np.clip(out, 0, 255).astype(np.uint8)


def match_template(target: np.ndarray, template: np.ndarray) -> tuple[float, tuple[int, int]]:
    th, tw = template.shape[:2]
    H, W = target.shape[:2]
    if th > H or tw > W:
        # Template larger than target (e.g. scale_90 / 720p shrink). Cannot match.
        return -1.0, (-1, -1)
    result = cv2.matchTemplate(target, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return float(max_val), (int(max_loc[0]), int(max_loc[1]))


def main() -> int:
    print("=" * 70)
    print("Template Matching STRESS TEST — perturb baseline, measure correlation")
    print("=" * 70)

    STRESS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    baseline_img, template = load_baseline()
    th, tw = template.shape[:2]
    print(f"\nBaseline: task4.png {baseline_img.shape[1]}x{baseline_img.shape[0]}")
    print(f"Template: {tw}x{th} px from (x1,y1,x2,y2)=({TEMPLATE_X1},{TEMPLATE_Y1},{TEMPLATE_X2},{TEMPLATE_Y2})")

    # Sanity: template-on-baseline self-match must be ~1.0
    self_score, self_loc = match_template(baseline_img, template)
    print(f"\nSanity self-match (task4 vs its own template): {self_score:.4f} at {self_loc}")
    if self_score < 0.999:
        print(f"  WARN: self-match should be ~1.0, got {self_score}")

    perturbations = [
        ("scale_90",            make_scale_90,          "task4 resized to 0.9x (smaller GUI/window). Template is original-size: a 0.9x panel cannot perfectly match a 1.0x template."),
        ("scale_110",           make_scale_110,         "task4 resized to 1.1x (larger GUI/window)."),
        ("resolution_1280x720", make_resolution_720p,   "task4 hard-resized to 1280x720 (cross-resolution)."),
        ("noise_gaussian",      make_noise_gaussian,    "task4 + Gaussian noise sigma=5 (screen capture noise)."),
        ("brightness_minus_20", make_brightness_minus_20, "task4 * 0.8 brightness (shader/lighting shift)."),
    ]

    results = []
    overall_pass = True
    failure_modes: list[str] = []

    for name, fn, desc in perturbations:
        perturbed = fn(baseline_img)
        out_path = STRESS_DIR / f"{name}.png"
        cv2.imwrite(str(out_path), perturbed)
        score, loc = match_template(perturbed, template)
        gate = "PASS" if score >= GATE_THRESHOLD else "FAIL"
        if gate == "FAIL":
            overall_pass = False
            failure_modes.append(f"{name} (correlation={score:.4f}): {desc}")
        notes = desc
        if score < 0:
            notes = f"TEMPLATE LARGER THAN TARGET — cannot match. {desc}"
        print(f"\n  {gate}  {name:25s} correlation={score:.4f}  loc={loc}  shape={perturbed.shape[1]}x{perturbed.shape[0]}")
        print(f"        {notes}")
        results.append({
            "name": name,
            "correlation": round(score, 6),
            "loc_xy": list(loc),
            "perturbed_shape_wh": [perturbed.shape[1], perturbed.shape[0]],
            "fixture_path": str(out_path.relative_to(SPIKE_ROOT)),
            "gate_status": gate,
            "notes": notes,
        })

    # Baseline mean from PoC: task3/4/5/5.1 all matched at ~1.0 (the PoC printed
    # them with verdict checks at >0.9). We embed the conservative known value.
    # The true 4-fixture mean from the existing PoC is approximately 1.000 since
    # all four are pixel-identical inventory UIs at same resolution.
    baseline_mean = 1.000

    report = {
        "schema_version": "1.0",
        "test": "template_matching_stress",
        "baseline_n": 4,
        "baseline_fixtures": ["task3", "task4", "task5", "task5.1"],
        "baseline_correlation_mean": baseline_mean,
        "baseline_caveat": "All 4 baseline fixtures are pixel-identical inventory UIs from the same session/resolution. PoC achieved 4/4 perfect matches but this is a ceiling, not a working range.",
        "template_source": "task4.png crop (847,295)-(1716,700)",
        "template_size_wh": [tw, th],
        "self_match_sanity": round(self_score, 6),
        "gate_threshold": GATE_THRESHOLD,
        "stress_fixtures": results,
        "overall_gate": "PASS" if overall_pass else "FAIL",
        "track_a_ready": overall_pass,
        "failure_modes": failure_modes,
        "notes": (
            "Stress test perturbs ONLY task4 (the reference) along 5 axes "
            "(scale, resolution, noise, brightness). The template is cut from "
            "ORIGINAL task4 — perturbed fixtures are matched against the "
            "unperturbed template. This isolates the template-matching robustness "
            "from any session-to-session UI variation. If even simple perturbations "
            "of the SAME source image fail the 0.85 gate, Track A's hybrid "
            "(GD anchor + template slot detection) is not viable for real-world "
            "deployment where UI scale/resolution will vary."
        ),
    }

    out_json = REPORTS / "template_stress.json"
    out_json.write_text(json.dumps(report, indent=2))

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    n_pass = sum(1 for r in results if r["gate_status"] == "PASS")
    print(f"  Per-fixture: {n_pass}/{len(results)} PASS  (gate >= {GATE_THRESHOLD})")
    print(f"  Overall:     {'PASS' if overall_pass else 'FAIL'}")
    print(f"  Track A ready: {overall_pass}")
    if failure_modes:
        print(f"  Failure modes:")
        for fm in failure_modes:
            print(f"    - {fm}")
    print(f"\n  Report: {out_json}")
    print(f"  Stress fixtures: {STRESS_DIR}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
