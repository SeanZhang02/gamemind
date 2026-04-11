# GameMind — Phase C-0 Probe

**Status**: probe infrastructure complete, smoke tested, waiting for Sean's
manual screenshot-capture + labeling session.

## What this is

The Phase C-0 go/no-go gate for GameMind. It measures whether
**Qwen2.5-VL-7B at Q4_K_M** can read Minecraft well enough to serve as the
perception layer for the agent. If it passes, Phase C build begins.
If it fails, we pick from the D1-D5 fallback chain in
`../../gamemind-final-design.md` §8.

## Environment confirmed

| Component                       | Status                                           |
| ------------------------------- | ------------------------------------------------ |
| Ollama                          | ✓ running at http://127.0.0.1:11434 (v0.13.1)    |
| qwen2.5vl:7b                    | ✓ pulled (6.0 GB, Q4_K_M) — baseline             |
| qwen3-vl:8b-instruct-q4_K_M     | ✓ pulled (6.1 GB) — A/B candidate                |
| qwen3-vl:8b-thinking-q4_K_M     | ✓ pulled (6.1 GB) — **do NOT use** (latency)     |
| GPU                             | ✓ RTX 5090, 32 GB VRAM, all variants fit         |
| Processor                       | ✓ 100% GPU (no CPU offload)                      |
| Python                          | ✓ 3.11 via uv                                    |
| Probe harness                   | ✓ smoke tested on all 3 variants                 |

## Smoke test results (synthetic fixtures, 2026-04-10)

Three models tested end-to-end on 3 synthetic images (N=3 is too small to
trust latency percentiles — these prove pipeline function, not rank models):

| Model                                  | p50    | p90    | JSON  | Notes                   |
| -------------------------------------- | ------ | ------ | ----- | ----------------------- |
| qwen2.5vl:7b                           | ~290ms | ~480ms | 100%  | baseline, battle-tested |
| qwen3-vl:8b-instruct-q4_K_M            | ~480ms | ~580ms | 100%  | A/B candidate           |
| qwen3-vl:8b-thinking-q4_K_M (default)  | 1462ms | 2100ms | 100%  | **FAILS gate**          |
| qwen3-vl:8b-thinking (think=false)     | 864ms  | 1195ms | 100%  | harness forces this     |

Key findings:
1. All variants work end-to-end with `format: json`. JSON parse reliability
   is 100% across the board on smoke fixtures.
2. The thinking variant in default mode exceeds the 1500 ms p90 gate. The
   probe harness sets `think: false` defensively, which recovers it, but the
   cleanest recommendation is: **only run instruct variants**.
3. On N=3 synthetic images, qwen2.5 and qwen3-instruct latency differences
   are within run-to-run noise (~200 ms variance). Real fixtures with 20+
   samples will give reliable numbers.
4. The warmup path was tuned to eliminate a 2.5 s first-image cold start by
   issuing two warmup passes with the actual task prompt shape.

**Decision**: run both qwen2.5vl:7b AND qwen3-vl:8b-instruct-q4_K_M on your
real 20-image fixtures and compare empirically. See `fixtures/LABELING_GUIDE.md`
for the dual-run instructions.

## What Sean does next (~1.5-2 hours)

Everything you need is in `fixtures/LABELING_GUIDE.md`. TL;DR:

1. Open Minecraft Java Edition in borderless windowed mode
2. Capture 20+ hard-case screenshots across T1-T4 categories (detailed
   checklist in the guide)
3. Save screenshots into `fixtures/`
4. Copy `fixtures/groundtruth.example.json` to `fixtures/groundtruth.json`
   and fill in one entry per screenshot using the schema shown there
5. Run the probe:
   ```bash
   cd "C:/Claude Code Beta/gamemind/phase-c-0"
   py -3.11 -m uv run python -m probe.run
   ```
6. Read the summary table at the bottom, decide PASS → Phase C build, or
   FAIL → pick a D1-D5 fallback

## Layout

```
phase-c-0/
  probe/
    __init__.py
    tasks.py              # T1-T4 prompts + scoring functions
    client.py             # Ollama HTTP wrapper with tuned warmup
    run.py                # main entry: load GT, run probe, print report
    gen_smoke_fixtures.py # generator for the synthetic smoke images
  fixtures/
    LABELING_GUIDE.md     # Sean's step-by-step manual guide
    groundtruth.example.json  # template showing all 4 task schemas
    groundtruth.smoke.json    # 3 synthetic items (for pipeline smoke test)
    smoke_t1.png              # synthetic smoke fixture (not Minecraft)
    smoke_t3.png              # synthetic smoke fixture
    smoke_t4.png              # synthetic smoke fixture
  results/                # probe reports land here (timestamped JSON)
  pyproject.toml          # uv project, Python 3.11, requests+pillow+pydantic
```

## Pass gate (for reference)

| Metric                      | Threshold         |
| --------------------------- | ----------------- |
| T1 block id accuracy        | ≥ 50% (hard floor)|
| T2 inventory read accuracy  | ≥ 70%             |
| T3 UI state accuracy        | ≥ 70%             |
| T4 spatial reasoning acc.   | ≥ 70%             |
| p90 inference latency       | ≤ 1500 ms         |
| JSON parse reliability      | ≥ 95%             |

All must pass. See `../../gamemind-final-design.md` §6 and §8 for rationale
and the D1-D5 fallback decision tree.

## Re-running the smoke test

If you want to confirm the pipeline still works before your real capture
session, run:

```bash
py -3.11 -m uv run python -m probe.run \
  --groundtruth fixtures/groundtruth.smoke.json \
  --fixtures fixtures
```

Expect **PASS (partial)** in ~10 seconds of wall-clock plus the ~3 s model
warmup. If you see FAIL for anything other than `t2_inventory SKIP`,
something changed — check `results/report-*.json` for the details.
