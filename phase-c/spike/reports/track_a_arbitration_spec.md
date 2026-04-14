# Track A Arbitration Spec — CV + GD Hybrid Layer 1

**Status**: DRAFT — answers the 5 unscoped points the investigation report flagged for Track A (Q2). Should be reviewed via `/plan-eng-review` before any Track A code is written.

**Why this exists**: investigation report estimated Track A at "2 days but realistically 3–4 days" because arbitration logic, slot-coord generalization, item sprite matching, fail-open behavior, and Layer 2 handoff were all unscoped. This document scopes them.

---

## 1. Producer model

Layer 1 has **two perception producers** running on every frame (or every Nth frame for GD if latency gate forces it):

| Producer | Tech | Strengths | Weaknesses |
|----------|------|-----------|------------|
| **CV-Anchor** | `cv2.matchTemplate` + slot-coord YAML | Pixel-fixed UI chrome (inventory_grid, hotbar, crafting_grid_NxM); 0 GPU; <5ms; deterministic | Brittle to GUI scale / resolution; doesn't classify slot *contents* |
| **GD-Zero-Shot** | Grounding DINO-tiny | World entities (trees, doors, players); flexible vocabulary | F1=0.15 on Minecraft; FP-prone; 70–500ms latency |

Both producers emit the same `Detection` schema:

```python
@dataclass(frozen=True)
class Detection:
    cls: str                    # class name from per-game vocab
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) in screen pixels
    confidence: float           # [0.0, 1.0]
    producer: Literal["cv", "gd"]  # provenance for arbitration
    metadata: dict              # producer-specific (e.g. template_score, gd_logits)
```

WorldModel ingests detections from both producers and runs **arbitration** before exposing a single coherent state.

---

## 2. Arbitration rules

### Rule 1: Class authority

Each class has a **canonical producer** declared in the per-game adapter YAML:

```yaml
# adapters/minecraft.yaml
class_authority:
  inventory_grid: cv      # CV is authoritative if it fires
  crafting_grid_2x2: cv
  crafting_grid_3x3: cv
  hotbar: cv
  output_slot: cv
  slot_grid: cv           # CV produces slot bbox positions
  item_in_slot: hybrid    # CV gives slot bbox, sprite-matcher classifies content
  tree: gd
  oak_log: gd
  grass_block: gd
  player: gd
  zombie: gd
  health_bar: cv          # HUD frame is fixed pixel position
  hunger_bar: cv
```

**If the canonical producer fires**: use its detection, **discard the other producer's detection of the same class**.
**If the canonical producer is silent**: fall through to the secondary producer (with confidence penalty — see Rule 3).

### Rule 2: Cross-producer NMS

When **different classes** (or one class with multiple instances) overlap spatially, run NMS:

```
for det_a, det_b in pairs(all_detections):
    if det_a.cls != det_b.cls and IoU(det_a.bbox, det_b.bbox) > 0.7:
        # Likely the same physical object detected as different classes
        # Keep the one with higher (confidence × producer_authority_weight)
        keep = max(det_a, det_b, key=lambda d: d.confidence * authority_weight(d))
        discard = the other
```

`authority_weight(cv) = 1.2`, `authority_weight(gd) = 1.0` — slight CV bias because CV's pixel-perfect matching is more reliable when it fires than GD's probabilistic detection.

### Rule 3: Fall-through confidence penalty

If a class's canonical producer is silent and the secondary producer fires, the detection's confidence is multiplied by 0.7 before exposure. Example: GD detects what looks like an `inventory_grid` (cls='inventory_grid', conf=0.6) but CV's template match was below threshold. Exposed confidence = 0.42, which is below most consumer thresholds, so the detection effectively flags as "uncertain — VLM should verify".

This is the **degradation signal** for Layer 2 (Qwen3-VL) and Layer 3 (Brain).

---

## 3. Slot-coord generalization

The investigation report's concern: calibration YAML hard-codes `(847, 295)` for ONE GUI type at ONE resolution. Minecraft has ~15 GUI types (inventory, 2x2 craft, 3x3 craft, furnace, chest, double chest, anvil, beacon, brewing, enchanting, hopper, dispenser, dropper, loom, smithing). Each needs its own slot-grid spec.

### Solution: parameterize as `(anchor, grid)`, not absolute coords

```yaml
# adapters/minecraft.yaml
gui_specs:
  inventory_open:
    detection_template: templates/minecraft/inventory_open_anchor.png
    anchor_offset: [0, 0]   # template's top-left = GUI's top-left
    grids:
      main_inventory:
        origin: [8, 84]     # relative to anchor
        cell_size: [18, 18]
        rows: 3
        cols: 9
      hotbar:
        origin: [8, 142]
        cell_size: [18, 18]
        rows: 1
        cols: 9
      craft_2x2_input:
        origin: [98, 18]
        cell_size: [18, 18]
        rows: 2
        cols: 2
      craft_2x2_output:
        origin: [154, 28]
        cell_size: [26, 26]
        rows: 1
        cols: 1

  crafting_table_open:
    detection_template: templates/minecraft/crafting_table_anchor.png
    grids:
      craft_3x3_input:
        origin: [30, 17]
        cell_size: [18, 18]
        rows: 3
        cols: 3
      craft_3x3_output:
        origin: [124, 35]
        cell_size: [26, 26]
        rows: 1
        cols: 1
      # main_inventory + hotbar still rendered, reuse same offsets
```

CV-Anchor pipeline:

```
1. Pyramid-scale match each gui_spec.detection_template against the screen
   (scales: 0.8, 0.9, 1.0, 1.1, 1.2 — handles GUI scale slider 1-5)
2. Pick the GUI type with highest cross-scale max correlation > 0.85
3. Compute anchor_xy = (match_top_left + anchor_offset) × scale_factor
4. For each grid in that GUI's grids:
     For each (row, col):
       slot_bbox = (anchor_xy + origin + (col×cell_w, row×cell_h),
                    anchor_xy + origin + ((col+1)×cell_w, (row+1)×cell_h)) × scale_factor
       emit Detection(cls='slot:{grid_name}:{row},{col}', bbox=slot_bbox, conf=anchor_correlation, producer='cv')
```

This gives **slot positions for all 15 GUI types from 15 anchor templates + 15 YAML grid specs**, no per-GUI Python.

### Stress test gate

Track A is NOT ready until the template_stress.py P0.3 benchmark passes ≥0.85 correlation across:
- GUI scale 0.9, 1.1
- Cross-resolution (1280x720 from 1920x1080 source)
- Brightness shifts ±20%
- Gaussian noise σ=5

If P0.3 fails on any axis, the YAML approach above is brittle and Track A needs pyramid-match hardening or a learned anchor detector.

---

## 4. Item-in-slot sprite matching subsystem

The biggest unscoped subsystem. CV gives slot bboxes, but "what is in this slot" is a separate problem. Three options ranked by cost/reliability:

### Option A: Sprite library matching (recommended)

Pre-extract every Minecraft item icon (vanilla = ~600 sprites) at native render size into `sprites/minecraft/`. For each slot bbox:

```
slot_crop = screen[y1:y2, x1:x2]
for sprite_name, sprite_img in sprite_library:
    score = cv2.matchTemplate(slot_crop, sprite_img, cv2.TM_CCOEFF_NORMED).max()
    if score > best:
        best, best_name = score, sprite_name
if best > 0.9:
    emit Detection(cls='item_in_slot', bbox=slot_bbox, conf=best,
                   metadata={'item_name': best_name}, producer='cv')
else:
    emit Detection(cls='slot_unknown_content', bbox=slot_bbox, conf=0.5, producer='cv')
```

**Cost**: ~600 sprites × 18x18 px template match × N slots per frame. With C-optimized cv2 + integral image: ~10–30ms total per frame even for full inventory. Acceptable.

**Coverage**: works for vanilla items only. Mods / resource packs / enchanted items (with overlay glint) need `score > 0.85` and metadata flagging.

**Generalization to Delta Force**: same approach with FPS sprite library (weapon icons, ammo types, medkits) — but FPS inventory items have varied art (different gun camos), so threshold may need to be lower (0.75) and fall through to VLM more often.

### Option B: Slot-crop OCR (for stack count only)

Stack count digits in slot corners are pixel-perfect. Run cv2 + tesseract on the bottom-right 8x8 corner of each slot to extract stack count (1–64). Cheap, deterministic, valuable for inventory-state reading.

### Option C: VLM fallback for unknowns

When sprite library returns `slot_unknown_content`, queue the slot crop for Layer 2 Qwen3-VL with prompt: "What single Minecraft item is shown in this 18x18 icon? One word answer." Cache results by sprite hash to avoid re-querying.

**Recommended Track A scope**: implement A + B in adapter YAML; defer C to Phase 2 because it crosses Layer 1/2 boundary.

### Track A budget delta

Investigation said 3–4 days. Adding sprite matching subsystem: **+1 day** (mostly sprite extraction + threshold tuning, not new algorithms). Realistic Track A budget: **4–5 days**.

---

## 5. Fail-open behavior

What happens at each ambiguity boundary:

| Scenario | Detection state | Behavior | Reason |
|----------|-----------------|----------|--------|
| CV correlation 0.85–1.00 on a class | High-conf detection emitted, GD detection of same class discarded | Trust CV | Pixel match is unambiguous |
| CV correlation 0.70–0.85 | Detection emitted with `conf = correlation × 0.8`, GD detection of same class **kept** for adjudication | Both producers flag, downstream decides | Ambiguous CV, second opinion useful |
| CV correlation < 0.70 | CV detection suppressed entirely; GD detection (if any) used with full confidence | GD owns the class | CV signal too weak to trust |
| GD detection with conf < 0.3 | Suppressed | Don't flood WorldModel with garbage | GD's low-conf is mostly FP per Day 2 data |
| Both producers silent on a canonical-CV class | Emit `Detection(cls=..., bbox=last_known, conf=0.0, metadata={'stale': True})` | Stale-flag the last-known position for ≤2s, then drop | Allows brief occlusion / animation transitions without losing state |
| Sprite library returns no match for slot | `Detection(cls='item_in_slot', bbox=slot, conf=0.5, metadata={'item_name': 'unknown'})` | Layer 2 may pick this up for VLM enrichment | Slot is real; content is mystery |
| Pyramid match returns no GUI anchor at all | `WorldModel.gui_state = 'closed'` | No slot detections; world detections still flow | Game is in world view, not UI |

**Hard rule**: Layer 1 NEVER raises exceptions to Layer 2/3. All ambiguity is encoded in confidence + metadata. The Brain decides what to do with low confidence (ask VLM, retry, give up, etc.)

---

## 6. Layer 2 (Qwen3-VL) handoff contract

Layer 1 → Layer 2 interface:

```python
@dataclass(frozen=True)
class WorldFrame:
    timestamp_ms: int
    screen_resolution: tuple[int, int]
    detections: list[Detection]
    gui_state: Literal['closed', 'inventory', 'crafting_2x2', 'crafting_3x3', ...]
    cv_anchor_correlation: float | None  # max correlation if any GUI matched
    gd_inference_latency_ms: float
    cv_inference_latency_ms: float
    frame_dropped: bool  # True if GD inference was skipped this frame for latency budget
```

Layer 2 receives WorldFrame and may **enrich** it with VLM-derived attributes:

```python
@dataclass(frozen=True)
class EnrichedDetection(Detection):
    vlm_attributes: dict  # e.g. {'item_name': 'oak_log', 'count': 4, 'enchanted': False}
    vlm_confidence: float
    vlm_latency_ms: float
```

**Coordinate frame**: all bboxes are in screen pixels, top-left origin. No normalization.

**Confidence semantics**:
- `confidence` from CV producer = template correlation (0.0–1.0)
- `confidence` from GD producer = GD logit converted to probability (0.0–1.0)
- `confidence` from VLM enrichment = self-reported by VLM (forced to 0.0–1.0 via prompt)
- These are **not directly comparable** across producers — Layer 2/3 should never `max()` across them. Use producer-specific thresholds.

**Latency budget**:
- CV producer: ≤10ms per frame
- GD producer: ≤200ms per frame (gate from C-0)
- WorldFrame assembly: ≤5ms
- VLM enrichment (Layer 2): async, doesn't block WorldFrame emission

**Frame skip protocol**: if GD inference exceeds 250ms (gate breach + buffer), Layer 1 emits `WorldFrame(frame_dropped=True)` and skips GD on the next frame to recover. CV producer always runs.

---

## 7. Open questions to be answered before Track A code

These are the things `/plan-eng-review` should challenge:

1. **Is per-GUI YAML sustainable for non-Minecraft games?** Delta Force has multiplayer UI that changes mid-frame (kill feed, chat). YAML may not capture all of it.
2. **Sprite library size for Delta Force**: Minecraft's ~600 vanilla sprites are well-defined. Delta Force has gun skins / camo variants / attachment-modified silhouettes. May explode to thousands.
3. **Frame-skip protocol vs. WorldModel staleness**: if GD skips 3 frames in a row, last_known positions are 600ms old. Is that acceptable for the 跑刀 task? Probably yes (looting is slow), but unclear for combat.
4. **Adapter spec language**: YAML is fine for static specs. If anything needs computation (e.g. "this slot is only valid when furnace is fueled"), do we keep YAML + computed-fields, or switch to a per-game DSL? **Hard rule from CLAUDE.md**: no per-game Python. Computed fields would have to be in YAML or a constrained expression language.
5. **Testing**: how do we unit-test arbitration rules without live game? Need a synthetic Detection-stream fixture format — separate spec needed.

---

## Next steps

1. P0.3 template stress test (already in flight) — gates whether the YAML approach survives perturbation
2. P1 OWLv2 baseline (already in flight) — may reveal an alternative GD producer that closes the `item_in_slot` gap zero-shot, simplifying §4
3. Track D Delta Force baseline (waits on Sean's 10 fixtures) — gates whether per-game adapter approach actually generalizes
4. After all 3 P0/P1 results in: `/plan-eng-review` on this spec, then code

**Do not start Track A code until P0.3 + P1 + Track D have results.** This document is a placeholder, not an execution plan.
