# Phase 1 Spike — Grounding DINO zero-shot validation

**Status**: Day 1 setup · 2026-04-13
**Purpose**: verify GD can detect Minecraft world objects (tree/cow/oak_log) AND UI elements (inventory slot) before committing to Phase 2 architecture.
**Gate** (go/no-go to Phase 2):
- World detection precision >= 0.5 @ IoU 0.5 on hand-labeled fixture set
- UI detection "at least localizable" OR CV fallback viable
- Inference latency p90 <= 200 ms/frame on RTX 5090
- Peak VRAM < 28 GB with GD + gemma4 26B MoE q4_K_M co-loaded (Layer 1 per docs/MODEL_DECISION.md)

**Kill-switch**: if world precision < 0.5 after 2 days → pivot to OWLv2 / Florence-2 / fine-tuned YOLO

See `research_gd_variants.md` for full technical rationale.

## Layout

```
phase-c/spike/
  pyproject.toml          # isolated dep set — does NOT touch main gamemind/ deps
  research_gd_variants.md # pre-spike research (confidence 6/10)
  sanity/
    __init__.py
    detect.py             # Day 1: load GD + detect on 1 frame
  fixtures/               # Minecraft screenshots (user-captured)
    world/                # tree, cow, oak_log scenes
    ui/                   # inventory open, crafting table
```

## Running

```bash
cd phase-c/spike
uv sync
uv run python -m sanity.detect --fixture fixtures/world/tree1.png --prompts "tree . oak_log . cow"
```

## Isolation rationale

Spike lives in `phase-c/spike/` with its own `pyproject.toml`. This prevents Phase 1 exploratory deps (torch cu128 nightlies, grounding-dino-pytorch, supervision) from contaminating the main gamemind package dep tree until we've validated they actually work. Successful Phase 1 → promote deps into main `pyproject.toml` during Phase 2.
