# Phase 1 Investigation Report (2026-04-14)

**Audit scope**: fresh-context review of Phase 1 spike (12 commits, 2 days).
**Reviewer posture**: adversarial — finding holes, not cheerleading.

---

## Overall verdict

Phase 1 **correctly falsified the original bet**: GD-tiny zero-shot on Minecraft is a failure (micro P=0.21, R=0.12, F1=0.15; GD-base is *worse* on false positives — 81 FP vs 49). The gate thresholds in `README.md` (world precision >= 0.5) were not met on either model size. That is a clean, honest empirical result and the spike did its job.

The **architectural pivot** is well-grounded: only 2 classes (`inventory_grid` F1=0.86, `grass_block` F1=0.67) cleared any reasonable bar, and the template-matching PoC on UI shows a deterministic-CV fallback is trivial. This is the right read.

What's **under-supported** is the narrative that this generalizes to Sean's actual target domain (Delta Force / realistic FPS). The logical chain "GD failed on Minecraft pixel-art -> GD will succeed on photorealistic FPS" is plausible but **unverified on our fixture set**, and the evidence we can cite externally is indirect. That deserves a caveat before any Phase 2 commitment.

---

## 5 key questions answered

### Q1. Template matching PoC — defensible on n=4 fixtures?

**No, not yet.** Score 0.96–1.00 is real but the sample is trivially small and deliberately controlled: all fixtures are 2560x1400 captures from the same session with identical UI state. The PoC has **not been tested against**:

- Window resize / DPI change / GUI scale slider (Minecraft has 5 GUI scales — each re-rasterizes UI at different integer multiples; template correlation collapses below ~0.7 on scale change)
- Different inventory *contents* (template uses top half 847,295–1716,700 which includes the 2x2 crafting grid; if that region has items, the correlation drops — not tested)
- Gamma/brightness shifts (screen recording software, HDR, night filter)
- Anti-aliasing differences (the template was cut from task4 which may have different AA settings than other sessions)
- Translucent overlays (chat, F3 debug, Optifine mods, resource packs)
- Cross-resolution transfer (1920x1080, 3440x1440 ultra-wide)

**The 0.96+ number is a ceiling, not a working-range estimate.** The right next step is a 30-fixture stress battery that specifically perturbs each of the above. The calibration-constants approach has the same brittleness: the YAML hard-codes `(847, 295)` — one GUI scale change invalidates every slot coord. Pyramid-scale matching + DPI-aware anchor detection is needed before this is production-ready.

### Q2. Track A (Hybrid) 2-day estimate — realistic?

**Optimistic by 1.5–2x.** The "write a hybrid Layer 1" framing hides several silent costs:

1. **Arbitration logic** between CV-anchor and GD outputs when both fire on the same region (inventory_grid GD F1=0.86 already, plus CV panel detector = duplicate detections). Need explicit priority rules + NMS across two producers.
2. **Slot-coord math generalization**: calibration YAML covers *one* resolution. Producing coords for 2x2, 3x3, furnace, chest, anvil, etc. — each is a separate template + slot-grid spec. Minecraft has ~15 GUI types.
3. **Item-in-slot sprite matching**: once you have slot bboxes from CV, you still need to classify what's inside (is this coal or iron? oak_log or spruce_log?). That's a second CV/VLM subsystem not currently scoped in Track A.
4. **Fail-open behavior**: what happens when template correlation is 0.75 (ambiguous)? Fall back to GD? VLM? Raise exception? Each branch needs unit tests.
5. **Integration surface with Layer 2 Qwen3-VL**: the handoff contract (what fields, what coordinate frame, what confidence semantics) isn't designed yet.

Realistic: **3–4 days** for a defensible Track A, not 2. Sean should budget the slip explicitly.

### Q3. Track D (Delta Force / realistic FPS) — "GD will be better on photorealism"

**Plausible but weakly supported.** The evidence we have:

- GD was pre-trained on photographic data (COCO, O365, RefCOCO, GoldG). Photorealistic FPS renders are closer to that distribution than Minecraft pixel-art. That's a real prior.
- **Counter-evidence**: GD drops to 29.8 mAP on underwater imagery (distribution shift even *within* photography). Rendered-game shaders introduce a domain gap that is smaller than Minecraft's but nonzero.
- **The public FPS detection work is all supervised YOLO fine-tunes**: CS2 YOLOv10s hits 0.958 precision / mAP50 0.979 — but that is fine-tuned on CS2 screenshots, not zero-shot. Zero-shot Valorant/CS2 benchmarks for GD/OWLv2 are **not published** (same gap as Minecraft).
- **Category gap**: "enemy player with tactical gear in urban rubble" is a compositional prompt. GD's compositional grounding is strong *on things that look like COCO person*, weaker on "operator in plate carrier".

**Honest confidence**: zero-shot GD on Delta Force is probably **better than 0.15 F1, worse than 0.70 F1**. That is a very wide interval. The right move for Track D is: grab 10 Delta Force fixtures, label 3 classes (player_enemy / player_friendly / weapon), run the same eval harness we just built, and measure. **Don't commit architecturally on a hand-wave.**

### Q4. Multi-track + timebox — executable?

**High scope-creep risk.** Three tracks × "we'll timebox each" is a classic trap:

- No single track has been sized against wall-clock (the 2-day Track A is optimistic; Track D needs fixture collection before code; Track B/C/etc. unspecified in scope).
- Single-person execution (Sean) means tracks are sequential, not parallel. Calling them "tracks" implies concurrency that doesn't exist.
- Context-switching cost between tracks is nonzero — every switch loses ~2h of warm-up.

Recommendation: **pick ONE track as primary, do a 2h spike on each other track to de-risk**, don't actually execute multiple in parallel. "Multi-track timeboxed" has failed in Phase A/B on this project before; see workflow discipline memory.

### Q5. n=11 fixtures — enough to drive a Track decision?

**For the kill decision (GD fails), yes.** 96 FN on 109 GT at IoU 0.5 is a signal nobody could argue with.

**For the direction decision (which Track), no.**
- `tree` class: 10 GT across ~5 fixtures — classifier-level data, but GD's 0% recall here is driven by the pixel-art silhouette ambiguity, which doesn't tell us what happens on realistic tree renders.
- `item_in_slot`: 34 GT, 0 TP — but this failure is **diagnostic of prompt fit**, not capacity. GD cannot localize tiny sprites inside a grid no matter how much data.
- `wooden_pickaxe`, `zombie`, `crafting_table`: 2 GT each — statistically useless, one fixture flip changes the headline.
- UI classes besides `inventory_grid`: hotbar/health/hunger — HUD-overlay prompts are OOD for photographic-trained GD; this is interesting signal but 5 fixtures per class is marginal.

Net: the **quantitative F1 numbers past the 3 headline classes are noisy**. Any Track decision citing `crafting_table` F1 or `wooden_pickaxe` F1 is overfitting to noise.

### Q6. Cost risk (Sean: "no 7–8 APIs, no $100/hr burn")

**Currently low, but 2 inflation vectors lurking.**

External dependencies right now:
- Ollama local (zero marginal cost, already Phase C-0 gated) ✓
- HF transformers GD download (one-time bandwidth) ✓
- No cloud API, no token billing ✓

**Watch items**:
1. **Grounding DINO 1.5 Pro** is API-only (no weights). If the pivot drifts toward "let's just try GD-1.5-Pro to see if it's better" that's a metered API — price not published, but in DINO-X/DINO-Edge family it's been quoted ~$0.001–0.01/image. At 5 FPS × 8h session that's $144–1440/day. **Hard no.**
2. **Qwen3-VL online variants** (via Alibaba cloud) would be the same trap. Stay on local `qwen3-vl:8b-instruct-q4_K_M` as locked in C-0.
3. Claude API (Layer 3 brain) is Max-Plan allowance — effectively free *if* we stay in-budget. At sustained 2-second-cycle brain calls this could hit Max limits. Need a rate-limit story before live play.

**Action**: add a CI guardrail that fails the build if any `http.*api.*dino|openai|anthropic|alibaba` URL appears in Layer 1/2 code paths. Fence the cost surface in code, not policy.

---

## Hidden risks / blind spots

1. **We haven't measured latency yet.** Gate says p90 ≤ 200ms on 5090. 12 commits, no latency.json in `reports/`. If GD HF port hits the documented 378–528ms (issue #31533), we're already blown past the gate even before the pivot. **Run this benchmark in the next session before anything else.**

2. **VRAM co-load not tested.** Gate says peak <28GB with GD + Qwen3-VL 8B q4_K_M loaded simultaneously. Evidence in reports/ is GD-only. We don't know if warm co-residency causes fragmentation, allocator thrash, or OOM under sustained load.

3. **Supervision / ByteTrack leak (#1164) unaddressed.** Research flagged it; spike code doesn't use Supervision yet so the leak surfaces only in Phase 2. If Track A ships without a tracker-reset story, hour-long sessions will OOM silently.

4. **`item_in_slot` 0/34 is a capability statement, not a prompt problem.** Template matching solves *slot positions*, not *what's in the slot*. The "slot → item class" problem is unsolved and currently unscoped. Track A's claim that "item_in_slot is a solved problem" is overstated in the PoC docstring.

5. **Fixture origin concentration.** All 11 fixtures from Sean's own Minecraft session, one resolution, likely one shader pack, one session's lighting. Any "it works" conclusion implicitly assumes this narrow distribution. At least 1 external-session fixture battery is needed before production claims.

---

## Action items for next session

- [ ] **Run latency benchmark first** — 100 frames, warmup 10, report P50/P90/P95. Gate-blocking.
- [ ] **Run VRAM co-load test** — GD-tiny + Qwen3-VL 8B simultaneously, 10min sustained, nvidia-smi log. Gate-blocking.
- [ ] **Stress-test template PoC**: add 5 perturbed fixtures (GUI scale change, different inventory contents, different resolution) and re-run. If correlation drops below 0.85 on any, the PoC is not ready.
- [ ] **Capture 10 Delta Force fixtures + label 3 classes**, run existing eval harness. This is the *actual* Track D feasibility signal, not research speculation.
- [ ] **Fence cost surface**: CI check that blocks external-API URLs in perception layer.
- [ ] **Write down Track A arbitration spec** before coding — CV-vs-GD priority, NMS, confidence fusion, fail-open behavior.
- [ ] **Address `item_in_slot` scope explicitly**: is sprite-template matching in Phase 1 or deferred? Right now it's ambiguous.
- [ ] **Pick ONE track as primary**, not three parallel tracks. Ship a 2h de-risk spike on each other track; don't build all three.
- [ ] **Run `/autoplan` before writing Phase 2 code.** Phase C kickoff discipline requires it (CLAUDE.md). Don't repeat Phase A's mistake.
- [ ] Delete or clearly mark the misleading PoC summary line `"inventory_grid F1=0.86 (6/6 TP) — template match should be >=100%"` — compares apples (detection F1) to oranges (anchor-match rate on 4 fixtures).

---

## Confidence score: 6/10

**Why 6 and not higher**: The *diagnostic* phase went well — the empirical gate did exactly what it was supposed to do and falsified the naive bet cleanly. That's +2 over baseline.

**Why 6 and not 9**:
- Latency + VRAM gates completely untested (-1)
- Template PoC sample n=4, not perturbed, over-claimed (-1)
- Delta Force feasibility is speculation without data (-1)
- Multi-track plan has documented scope-creep history on this project (-0.5)
- `item_in_slot` scope ambiguity (-0.5)

Path to 8/10 next session: run the two gate-blockers, stress-test the template PoC with perturbed fixtures, capture + eval 10 Delta Force fixtures. All four are <1 day of work. Do them before any Phase 2 architectural commit.
