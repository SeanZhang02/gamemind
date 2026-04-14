# Phase 1 spike — 2026-04-14 self-loop closeout

**Session goal**: auto-advance everything that did not require Sean's Delta Force screenshots, then stop.

**Status**: done. All P0/P1 benchmarks run, results adversarially audited, audit findings either fixed (rerun) or disclosed (spec patches). Pushed to `feat/phase-c-perception-spike` in 4 commits.

---

## Final gate results (post-audit, defensible)

| Gate | Result | Verdict |
|------|--------|---------|
| **Latency p90 ≤ 200ms** | single-pass 75.4ms / **multi-pass 229.0ms** | **🔴 FAIL** (multi-pass exceeds budget) |
| **VRAM peak < 28GB** | 21.6GB co-load over 10min, 374 ticks, 0 errors | ✅ PASS |
| **Template stress ≥ 0.85 corr** | 2/5 (noise + brightness PASS; scale 90/110 + cross-resolution FAIL) | 🔴 FAIL |
| **Track B OWLv2 closes GD gaps** | micro F1 0.049 << GD 0.152, item_in_slot 0/34, tree 0/10 | ❌ REJECTED |

Three of four go red. Day 1's "gate PASSED" claim is now retracted — same single-pass methodology error.

---

## What changed from the optimistic mid-session report

The first round of benchmark results (latency 79.4ms / OWLv2 0.031) had two methodology issues that an adversarial audit caught:

1. **Latency was single-pass** on the world group only (10 classes). Production `eval_harness --all-classes` runs **multi-pass** — one GD forward per prompt group whose classes overlap GT. A realistic mixed-scene frame (inventory + HUD + tree) triggers all 3 groups → 3× the latency. Multi-pass p90 = 229ms, blowing the 200ms gate.

2. **OWLv2 had asymmetric setup** (score_threshold 0.1 vs GD 0.2, no NMS) producing 712 FP and an artificially low F1. Fixed with symmetric threshold + class-wise NMS: F1 = 0.049, still much worse than GD, but now defensibly so.

The audit also found 4 logical holes in the Track A arbitration spec — patched in place (see `track_a_arbitration_spec.md` §2/§3/§4 + Rule 3 changes).

---

## What this means for next steps

### 🔴 Latency failure changes Phase 2 design

200ms budget at 5fps = 1s/sec compute → multi-pass at 229ms means GD cannot run every frame at full vocabulary. Three viable responses:

1. **Adaptive prompt selection**: only run prompt groups whose classes are likely present (e.g. skip `ui` group when inventory is closed). This is what the Layer 1 design should already do but the spec didn't make explicit. Free win — converts multi-pass back to ~single-pass for most frames.
2. **Frame skipping**: GD at 2-3fps instead of 5fps, fast detector covers gaps. Sustainable but loses temporal resolution.
3. **Quantize / smaller GD model**: GD-tiny is already smallest; further quantization (FP16 → INT8) could save 30-40% latency. Untested for accuracy impact.

The **honest read**: the 200ms gate was set during Phase B without empirical multi-pass data. Either revise the gate (e.g. 250ms with adaptive prompt selection) or commit to Option 1 in the Phase 2 design.

### ✅ VRAM headroom is fat

21.6GB peak / 28GB budget = 6.4GB headroom. Even with longer Qwen prompts in production, this gate is comfortable. Single most defensible result of the session.

### ❌ Track B (OWLv2) is dead

Symmetric fair eval still says F1 0.049 < GD 0.152. The `item_in_slot` 0/34 + `hotbar` 0/N pattern is identical to GD — confirms zero-shot domain limit on game-UI primitives is **universal across open-vocabulary detectors**, not GD-specific. Don't waste time on more zero-shot detector swaps.

### 🔴 Track A needs more than the original spec

Plain `cv2.matchTemplate` fails on scale ±10% / cross-resolution. Track A budget should grow from "2 days" (original) → "4-5 days" (this spec) → realistically **6-8 days** with the required 30-fixture stress battery, pyramid matcher, sprite library micro-benchmark, and feature-match fallback prep. None of which is started.

### 🟡 Track D (Delta Force) is the unblocker

Three of four gates red, but Track D — the photorealism hypothesis test — is the one that decides whether Minecraft is a representative testbed at all. **Sean's 10 screenshots remain the critical-path blocker.**

If Track D F1 > 0.4 → realism hypothesis holds, Track A's hybrid design has a target to harden against, Phase 2 design proceeds with adaptive-prompt-selection latency mitigation.
If Track D F1 < 0.3 → no zero-shot detector approach works on either domain. Forces fundamental rethink (per-game fine-tuned YOLO? VLM-only Layer 1? something else entirely?).

---

## Artifacts pushed (4 commits since last main checkpoint)

| Commit | What |
|--------|------|
| `4e9df31` | feat: P0/P1 benchmarks (4 scripts + initial JSON outputs + 5 stress fixtures) |
| `026a986` | docs: Track A arbitration spec v1 |
| `97fcd64` | docs: Delta Force capture guide |
| `ca8b35c` | fix: rerun benchmarks with adversarial-audit fixes — latency FAILS |

All on `feat/phase-c-perception-spike`, pushed to GitHub. Not merged to main (gate failures need design response first; not a merge candidate yet).

---

## What is NOT done (waiting on Sean)

1. **Track D Delta Force baseline** — needs 10 screenshots per `phase-c/spike/fixtures_delta/CAPTURE_GUIDE.md`. Predicted 15-20min capture time.
2. **Latency gate response decision** — Sean needs to either (a) approve adaptive prompt selection in Phase 2 design, or (b) revise gate to ≥250ms. Self-loop cannot decide architecture without owner.
3. **Track A 30-fixture stress battery + pyramid matcher implementation** — gated on Track D outcome; if Track D fails, Track A might not be worth hardening.
4. **`/plan-eng-review` on patched arbitration spec** — required before any Track A code.

---

## Adversarial audit findings (full record)

5 questions challenged, 5 verdicts:

| # | Audit finding | Action taken |
|---|--------------|---------------|
| Q1 | Latency single-pass underestimates production cost | **Reran** — multi-pass p90=229ms, gate FAILS |
| Q2 | VRAM workload not fully representative but headroom fat | Disclosed in closeout, no rerun (steady-state evidence is strong) |
| Q3 | Template stress missed content variation, GUI overlay, tooltip | Disclosed in spec §3 + flagged required follow-up; no rerun (verdict already FAIL) |
| Q4 | OWLv2 setup asymmetric (threshold + NMS) | **Reran** with fair setup — F1 still 0.049, REJECTED defensible |
| Q5 | Arbitration spec 4 logical holes | **Patched in place** — §2 NMS, §3 pyramid claim, §4 sprite latency, Rule 3 redundancy |

**Strongest finding**: VRAM 21.6GB co-load with dead-flat steady state.
**Weakest finding (now resolved)**: was OWLv2 REJECTED; rerun confirmed it.
**Surprise change**: Latency gate flipped PASS → FAIL.

---

**Bottom line for Sean**:
- The "two hard gates PASS" mid-session claim was wrong on latency. Real result: **1 of 2 hard gates passes**, and the other (latency) needs a design response, not just a smaller model.
- Track B is dead (cleanly).
- Track A needs significantly more work than originally scoped.
- Track D screenshots remain the next decision input.
- Self-loop hits its natural limit here — architectural decisions need Sean.
