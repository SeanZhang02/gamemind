# Phase C-0 Fixtures — Labeling Guide

This is **Sean's 2-hour manual step** for Phase C-0 of GameMind. The probe
harness (`probe/run.py`) is ready; it needs 20+ real Minecraft screenshots
with ground-truth labels to produce a meaningful pass/fail verdict.

## Why you are doing this

Phase C-0 is the single go/no-go gate before committing ~200 hours to Phase C
build. We are measuring whether **Qwen2.5-VL-7B at Q4_K_M quantization** can
read Minecraft well enough to be the perception layer. If it can't, we pick a
D1-D5 fallback from the final design doc instead of writing thousands of
lines of code on top of a broken assumption.

**There is zero public data on Qwen2.5-VL-7B's accuracy on Minecraft
screenshots.** Everything else is extrapolation from DocVQA / Android Control.
This probe is how we find out.

## Pass criteria (for reference)

| Metric                     | Threshold         |
| -------------------------- | ----------------- |
| T1 block id accuracy       | ≥ 50% (hard floor)|
| T2 inventory read accuracy | ≥ 70%             |
| T3 UI state accuracy       | ≥ 70%             |
| T4 spatial reasoning acc.  | ≥ 70%             |
| p90 inference latency      | ≤ 1500 ms         |
| JSON parse reliability     | ≥ 95%             |

All six must pass. Partial pass → fallback chain.

## What you're capturing: 20+ screenshots across 4 task categories

Aim for roughly **5-6 screenshots per category**, weighted toward hard cases.
The probe is worthless if every image is a clean easy shot — it needs to
stress the model.

### T1 — Block identification (target: 6 images)

Player is in first-person view. The crosshair (screen center) is pointing at
a specific block. You label which block it is.

Capture a mix of:
- [ ] 1x **easy**: oak log or stone in daylight, crosshair dead-on
- [ ] 1x **medium**: ore block in a cave with torch light (iron_ore, coal_ore)
- [ ] 1x **medium**: grass_block on a hill edge
- [ ] 1x **hard**: block at torch-lit cave range with visual noise
- [ ] 1x **hard**: block in water or partially obscured
- [ ] 1x **edge case**: crosshair on air/sky (expected: `air`)

Use F3 to verify the block type, then turn off F3 before capturing. Label using
the **exact canonical block id** (e.g. `oak_log`, `stone`, `iron_ore`, `grass_block`).

### T2 — Hotbar / inventory read (target: 4-5 images)

Screenshot should show the hotbar clearly. The model reads all 9 slots from
left to right, reporting item id and count.

Capture:
- [ ] 1x **easy**: 3-4 items, large stacks (32+), no visual clutter
- [ ] 1x **medium**: full hotbar with mixed stack sizes and some empty slots
- [ ] 1x **hard**: hotbar during combat or with particles overlaying slots
- [ ] 1x **hard**: inventory screen open (not just hotbar) with 20+ different items
- [ ] 1x **edge case**: all 9 slots empty (scrolling past first hotbar on fresh spawn)

Scoring is per-slot partial credit. Getting 7 of 9 slots right in a hotbar = 0.78 score.

### T3 — UI state classification (target: 5-6 images)

Label which screen/overlay is currently active. Expected values:
`hud_only | inventory_open | crafting_table | furnace | chest | main_menu | pause_menu | f3_debug | chat_open | death_screen`

Capture one of each:
- [ ] `hud_only` — normal gameplay, just hotbar+health visible
- [ ] `inventory_open` — pressed E
- [ ] `crafting_table` — right-clicked a crafting table
- [ ] `chest` — opened a chest (place one, load some items in)
- [ ] `f3_debug` — F3 overlay on
- [ ] `pause_menu` — pressed Esc
- [ ] `chat_open` — pressed T (optional, if time)

Spread across different backgrounds so the model can't cheat off the world scene.

### T4 — Spatial reasoning (target: 5-6 images)

The hardest category and the best signal for "can this model actually reason
about the world, not just pattern-match pixels."

Each label needs all 4 fields:
- `location`: `underground | above_ground | underwater`
- `hostile_visible`: `true | false`
- `hazard_visible`: `true | false`
- `hazard_type`: `lava | cliff | void | none`

Capture:
- [ ] 1x above-ground day, no hostiles, no hazards (baseline)
- [ ] 1x above-ground night with a hostile (zombie/skeleton) clearly visible
- [ ] 1x underground with lava within 5 blocks
- [ ] 1x standing on a cliff edge, big drop visible
- [ ] 1x underwater looking at seafloor
- [ ] 1x End or Nether void edge (hazard=void) — optional stretch case

T4 uses per-field scoring (partial credit). Getting 3 of 4 fields right = 0.75 score.

## How to capture screenshots

1. Minecraft Java Edition
2. Set game to **borderless windowed** mode (Video Settings → Fullscreen OFF,
   then drag-maximize the window). This is also required for Phase C capture
   so do it once and leave it.
3. Use **F2** in-game to save a screenshot to `%APPDATA%\.minecraft\screenshots\`.
   Or use Windows Snipping Tool (`Win+Shift+S`) for a more controlled crop.
4. Copy captured files into this directory:
   `C:\Claude Code Beta\gamemind\phase-c-0\fixtures\`
5. Name them meaningfully: `t1_oak_log_forest.png`, `t4_night_zombie.png`, etc.
   The file name only matters for your sanity while labeling.

## How to label

1. Copy `groundtruth.example.json` to `groundtruth.json` in the same directory.
2. For each screenshot, add one entry. Follow the examples for exact field
   shape per task category.
3. Set `id` to anything short and unique per entry.
4. Set `image` to the exact file name.
5. Set `category` to one of `t1_block | t2_inventory | t3_ui | t4_spatial`.
6. Set `difficulty` to `easy | medium | hard` — just for your own records,
   doesn't affect scoring.
7. Fill `expected` with the ground-truth answer using the exact schema the
   example shows for that category.

When in doubt, use F3 in-game to check the actual block id / coordinates / etc.

## How to run the probe

You are running the probe **twice**, against two different models, so you can
pick the better one by empirical data rather than benchmark hearsay. Both
models are already pulled to your machine.

From `C:\Claude Code Beta\gamemind\phase-c-0\`:

**Run 1 — Qwen2.5-VL-7B baseline** (the model the final design doc picked):

```bash
py -3.11 -m uv run python -m probe.run --model qwen2.5vl:7b
```

**Run 2 — Qwen3-VL-8B-Instruct alternative** (newer, benchmarks suggest
better on GUI + spatial tasks, roughly tied on OCR):

```bash
py -3.11 -m uv run python -m probe.run --model qwen3-vl:8b-instruct-q4_K_M
```

**IMPORTANT — model name discipline**:
- ✅ `qwen2.5vl:7b` — the original baseline
- ✅ `qwen3-vl:8b-instruct-q4_K_M` — the full tag, only variant you should test
- ❌ `qwen3-vl:8b` — default tag is ambiguous between variants, DO NOT USE
- ❌ `qwen3-vl:8b-thinking-*` — thinking variant does chain-of-thought reasoning
  that blows through the 1500ms latency gate on synthetic smoke tests
  (measured p90 = 2100ms without think=false). The probe harness sets
  `think: false` defensively, but the safest path is simply: don't use it.

Each run prints per-item progress (latency, JSON parse, score), then a summary
table with per-category accuracy, latency percentiles, gate checks, and a
PASS/FAIL verdict. A full JSON report is written to
`results/report-<timestamp>.json`.

**Expected wall-clock per run**: ~30-45 seconds first time (cold model load),
then ~5-8 seconds per image in steady state. 20 images ≈ 3-5 minutes per run.
Two runs = **6-10 minutes total**.

## Interpreting the two reports

After both runs, look at the summary tables side by side and decide:

1. **Same overall verdict (both PASS or both FAIL)**:
   - Both PASS → use the one with higher per-category accuracy on your weakest
     category. If tied, pick by lower p90 latency. If still tied, use
     `qwen2.5vl:7b` (battle-tested baseline).
   - Both FAIL on the SAME category → jump to D1-D5 fallback based on which
     category failed. Neither model can save us, so the decision is moot.
   - Both FAIL on DIFFERENT categories → this is information. Tell me which
     failed on each and I'll help pick the right fallback.

2. **Qwen3-Instruct PASSES, Qwen2.5 FAILS** → swap to Qwen3-Instruct. The
   empirical A/B justified the swap.

3. **Qwen2.5 PASSES, Qwen3-Instruct FAILS** → stay on Qwen2.5. The benchmarks
   lied or the quantization hurt Qwen3 on our specific task mix.

Either way: **trust the data from YOUR 20 real screenshots**, not benchmarks.
That's the whole point of the gate.

## Interpreting the result

- **Overall PASS** → You are clear to start Phase C build. Open a new
  Claude Code session and kick off Phase C with the final-design doc.
- **T1 ≥ 50% but some other category fails** → D1 fallback: prompt/adapter
  hints retry. Try rephrasing task prompts in `probe/tasks.py`, re-run.
- **T1 fails** or **many categories fail** → D2: upgrade to Qwen2.5-VL-32B
  Q4 (16-18GB VRAM, still fits the 5090). Re-run probe.
- **All 70B-class models fail** → D3: add Gemini 2.5 Pro as a cloud critic
  for hard cases, hybrid approach.
- **Vision is fundamentally not there** → D4: descope v1 target to a 2D
  pixel-art game (Stardew / Factorio / Vampire Survivors) where the vision
  problem is qualitatively easier.
- **Nothing works** → D5: scale back to a PoC with `--dev-checkpoint` manual
  mode and call v1 a learning exercise, not a framework.

Decision rule: **if p90 latency >> 1500ms but accuracy is fine**, look at
num_ctx + quantization tuning before escalating to D2. If accuracy is fine
but JSON reliability <95%, that's a prompt engineering problem, not a model
capability problem — fixable in minutes.

## Time budget

| Step                              | Time       |
| --------------------------------- | ---------- |
| Screenshot capture (20 images)    | 45-60 min  |
| Ground-truth labeling             | 20-30 min  |
| First probe run                   | 5 min      |
| Review report, iterate if needed  | 15-30 min  |
| **Total**                         | ~1.5-2 hrs |

That's the C-0 gate. Pass it, Phase C is go. Fail it, we have a decision tree
ready and no wasted build time.
