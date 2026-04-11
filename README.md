# GameMind

A universal game AI agent framework. v1 demo: Minecraft Java Edition,
single-player offline.

## What this is

GameMind plays video games via vision + OS-level keyboard/mouse input only.
No game APIs, no mods, no memory reads. The architecture is declarative over
two axes — games (YAML adapters) and models (OpenAI-compatible backends) —
so adding a new game means adding a YAML file, not a Python module.

**Status (2026-04-11)**: Phase C-0 (perception probe gate) has passed.
`qwen3-vl:8b-instruct-q4_K_M` via Ollama on RTX 5090 selected as Layer 1
perception model. Phase C build is the next step, gated on `/autoplan` being
run against the final design doc.

## Architecture (one paragraph)

Two-tier hybrid (ARCH-C / Alt B2). A small VLM runs locally at 1-2 Hz for
continuous perception (Layer 1). A bigger model (Claude Sonnet via API)
runs sparsely, woken by event gating (Layer 2) to do policy decisions
(Layer 3). Actions go through anti-cheat-safe input primitives (Layer 4,
`pydirectinput` scan codes via `SendInput`). A skill library learns across
sessions (Layer 5). Per-game knowledge lives in declarative YAML adapters
(Layer 6) — the wedge that differentiates GameMind from Cradle / Lumine /
UI-TARS-desktop.

Full design in [`docs/final-design.md`](docs/final-design.md).

## Three design rules (CI-enforced)

1. **No hand-authored coordinates in the action layer.** All targets come
   from runtime vision grounding. `grep` check in CI.
2. **No per-game Python in Layer 6.** Adapters are pure YAML. No
   `if game == "minecraft"` branches.
3. **Per-game prompts stay generic.** Prompt templates query adapter fields
   by name. Game knowledge lives in YAML data, not prompt prose. Violation
   test: if removing the game name from a prompt breaks task execution, the
   prompt is violating this rule.

## Repository layout

```
gamemind/
├── README.md                     ← you are here
├── CLAUDE.md                     ← project-level Claude Code instructions
├── docs/
│   ├── final-design.md           ← authoritative design doc (~480 lines)
│   └── sean-approval-package.md  ← locked-in decisions checklist
├── phase-c-0/                    ← perception probe (historical + regression asset)
│   ├── README.md
│   ├── C0_CLOSEOUT.md            ← probe gate outcome + model decision
│   ├── probe/                    ← Ollama wrapper + T1-T4 prompts + gate runner
│   └── fixtures/                 ← 18 real Minecraft screenshots + groundtruth.json
├── phase-c/                      ← (future) Phase C build lives here
└── .github/workflows/ci.yml      ← lint + import check
```

## Running the probe (regression asset, not required for build)

```bash
cd phase-c-0
py -3.11 -m uv run python -m probe.run --model qwen3-vl:8b-instruct-q4_K_M
```

Expected: `VERDICT: PASS` with T1=83% / T3=100% / T4=92% / p90<1500ms /
JSON=100%. T2 is reported as informational (non-blocking). See
`phase-c-0/C0_CLOSEOUT.md` for full rationale.

## Budget honesty

Phase C build is estimated at **205-315 hours / 9-11 weeks realistic / 14
weeks pessimistic**. Red line: 350 hours. If the project exceeds 350 hours
without a clear path to v1 done criteria, stop and redesign.

v1 is a personal tool. v2 (research artifact) is unlocked only if one of
four trigger events fires within 90 days post-v1: (1) 2+ games working
through pure YAML adapter delta, (2) Phase C-0 80%+ on all categories,
(3) structural third-game universality proof, (4) community fork or
external PR contribution.

## License

TBD (private repo during development).
