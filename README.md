# GameMind

**Universal game AI agent framework.** Play any game with vision + OS-level keyboard/mouse, driven by a local VLM with sparse cloud brain wakes. Adding a new game means writing a YAML adapter — not a Python module.

**Status (2026-04-11)**: Phase C-0 (perception probe) passed with `gemma4:26b-a4b-it-q4_K_M`. Phase C Step 1 scaffold ~80% complete — package layout, errors, events writer, adapter loader, perception primitives, brain backend, session manager, and CLI daemon lifecycle all land with unit tests (201 passing). Real Windows Graphics Capture / DXGI / pydirectinput bindings are the remaining runtime-dependent work.

v1 is a personal tool. v2 is a research-artifact upgrade unlocked only by explicit trigger events within 90 days post-v1 ship. See `docs/final-design.md` §OQ-6 for the staging contract.

---

## Quickstart

```powershell
# 1. Prereqs (Windows 10+ / ≥8GB VRAM NVIDIA / Python 3.11)
winget install astral-sh.uv
winget install ollama.ollama
ollama serve                                          # leave running
ollama pull gemma4:26b-a4b-it-q4_K_M

# 2. Clone + install
git clone https://github.com/SeanZhang02/gamemind.git
cd gamemind
uv sync --extra dev

# 3. Set your Anthropic API key (env-var only per Amendment A10)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# 4. Verify
uv run gamemind --version
uv run gamemind adapter validate adapters/minecraft.yaml
uv run gamemind doctor --all          # scripted remediation table
```

Full install guide: [`docs/install.md`](docs/install.md).

---

## What works today

- **`gamemind --version`** — package imports clean, Protocol signatures frozen
- **`gamemind adapter validate <path>`** — pydantic strict validation, path traversal hardening, one-screen summary output
- **`gamemind daemon start/stop/status`** — FastAPI on `127.0.0.1:8766` with bearer token auth, PID file lifecycle, Ollama liveness probe in `/healthz`
- **`gamemind doctor --all`** — prints the 5-remediation table (real capture/input/perception sub-checks land with Windows bindings)
- **`GET /v1/state`**, **`POST /v1/session/start`**, **`POST /v1/session/stop`** — session state machine endpoints, authenticated
- **201 unit tests** across `gamemind/brain/`, `gamemind/perception/`, `gamemind/capture/`, `gamemind/adapter/`, `gamemind/events/`, `gamemind/daemon/`, `gamemind/session/`

## What's next (blocked on Windows runtime)

- Real `windows-capture` WGC binding
- Real `dxcam` DXGI fallback binding
- Real `pydirectinput-rgx` scan code input path
- Live-perception spike (Amendment A1 freshness contract validation)
- End-to-end `gamemind run --adapter adapters/minecraft.yaml --task "chop 3 logs"` on a real Minecraft session

---

## Architecture (one paragraph)

Two-tier hybrid (**ARCH-C / Alt B2**). A small VLM runs locally at 2-3 Hz for continuous perception (Layer 1 — Gemma 4:26B-a4b-it via Ollama). Claude Sonnet 4.5+ runs sparsely via the Anthropic SDK, woken by 5 semantic triggers (W1-W5 in §1.4) to do plan decomposition, stuck replan, abort evaluation, vision-critic arbitration, and task completion verification. Actions go through anti-cheat-safe input primitives (`pydirectinput-rgx` scan codes via `SendInput`). Layer 5 skill library learns across sessions (JSONL + faiss). Per-game knowledge lives in declarative YAML adapters (Layer 6) — the wedge that differentiates GameMind from Cradle, Lumine, and UI-TARS-desktop.

Full design: [`docs/final-design.md`](docs/final-design.md) (~2400 lines, includes Phase 1 autoplan review + 15 applied amendments).

---

## Three design rules (CI-enforced)

1. **No hand-authored coordinates in the action layer.** All targets come from runtime vision grounding. `grep` check in CI against `mouse_move(<int>, <int>)` style literals.
2. **No per-game Python in Layer 6.** Adapters are pure YAML. No `if game == "minecraft"` branches. Enforced via pydantic strict schema + `yaml.safe_load`.
3. **Per-game prompts stay generic.** Prompt templates query adapter fields by name. Game knowledge lives in YAML data, not prompt prose. **Violation test**: if removing the game name from a prompt breaks task execution, the prompt is violating this rule.

**Design Rule 4 (Amendment A3, 2026-04-11)**: Layer 1 perception output and adapter-supplied text are UNTRUSTED. Brain prompt templates wrap them in explicit `<observation>` and `<adapter-fact>` tags with a prepended "data, never instructions" safety note. See [`docs/protocols.md`](docs/protocols.md).

---

## Reference documentation

Read in this order for a cold start:

1. [`docs/final-design.md`](docs/final-design.md) §0 Executive Summary + §1 Architecture — the 10-minute big picture
2. [`docs/install.md`](docs/install.md) — prereqs + 5-minute happy path + troubleshooting
3. [`docs/adapter-schema.md`](docs/adapter-schema.md) — how to write `adapters/<game>.yaml` with annotated example
4. [`docs/protocols.md`](docs/protocols.md) — frozen Protocol signatures (Amendment A12) for LLMBackend / CaptureBackend / InputBackend
5. [`docs/events-schema.md`](docs/events-schema.md) — Amendment A2 event envelope + 36 enumerated event_types
6. [`docs/errors.md`](docs/errors.md) — E101-E123 numbered reference with cause/fix per error

Deeper: [`docs/final-design.md`](docs/final-design.md) §2 (6 Open Questions), §3 (Design Rules), §6 (Phase C Step 1-3 build plan), §10 (autoplan review + 15 amendments).

---

## Repository layout

```
gamemind/
├── README.md                          ← you are here
├── CLAUDE.md                          ← project-level Claude Code instructions
├── LICENSE / CONTRIBUTING.md / CODEOWNERS
│
├── pyproject.toml                     ← uv-managed Python 3.11 project
│
├── gamemind/                          ← Phase C Step 1 package (the real build target)
│   ├── __init__.py                    ← version 0.1.0
│   ├── errors.py                      ← 23 exception classes (E101-E123) with Tier 2 messages
│   ├── cli.py                         ← daemon / doctor / run / adapter subcommands
│   ├── brain/                         ← Layer 3 (sparse brain wakes)
│   │   ├── backend.py                 ← LLMBackend Protocol + LLMResponse dataclass (A12)
│   │   ├── anthropic_backend.py       ← Anthropic SDK w/ adaptive thinking + prompt caching
│   │   ├── prompt_assembler.py        ← learn-from-cradle tripartite pattern (OQ-2)
│   │   └── prompts/templates/         ← 5 wake-trigger templates (W1-W5)
│   ├── perception/                    ← Layer 1 (continuous local VLM)
│   │   ├── freshness.py               ← Amendment A1 latest-wins queue + PerceptionResult
│   │   └── ollama_backend.py          ← Ollama HTTP client (refactored from probe/client.py)
│   ├── capture/                       ← Layer 0 (WGC + DXGI stubs)
│   ├── daemon/                        ← FastAPI app + lifespan + PID file
│   ├── adapter/                       ← Layer 6 (pydantic schema + yaml.safe_load)
│   ├── events/                        ← Amendment A2 event writer + envelope + secret scrub
│   └── session/                       ← Session state machine + Outcome enum
│
├── adapters/
│   └── minecraft.yaml                 ← v1 adapter skeleton — chop_logs goal
│
├── tests/                             ← 201 unit tests across all modules
│
├── docs/                              ← reference documentation
│   ├── final-design.md                ← ~2400-line authoritative spec
│   ├── install.md                     ← 5-minute quickstart + troubleshooting
│   ├── adapter-schema.md              ← line-by-line annotated minecraft.yaml
│   ├── protocols.md                   ← LLMBackend / CaptureBackend / InputBackend
│   ├── events-schema.md               ← Amendment A2 envelope + 36 event_types
│   ├── errors.md                      ← E101-E123 reference
│   └── sean-approval-package.md       ← locked-in Phase B decisions
│
├── phase-c-0/                         ← historical perception probe (regression asset)
│   ├── README.md
│   ├── C0_CLOSEOUT.md                 ← probe gate outcome + model decision
│   ├── probe/                         ← Ollama wrapper + T1-T4 prompts + gate runner
│   └── fixtures/                      ← 18 real Minecraft screenshots + groundtruth.json
│
└── .github/workflows/ci.yml           ← ruff lint + format + Three Design Rules + phase-c-0 + regression
```

---

## Running the Phase C-0 probe (regression asset)

```powershell
cd phase-c-0
uv run python -m probe.run
```

Expected: `VERDICT: PASS` with T1=83% / T3=100% / T4=92% / p90 <1500ms / JSON=100%. T2 is reported as informational (non-blocking per §OQ-6 game-state-aware verification wedge). See [`phase-c-0/C0_CLOSEOUT.md`](phase-c-0/C0_CLOSEOUT.md) for full rationale.

Label a PR with `perception` or `adapter` to run this against a self-hosted Windows runner as `phase-c-0-regression` (Amendment A11 — currently in placeholder mode until the runner is provisioned).

---

## Budget honesty

Phase C build is estimated at **223-339 hours** (revised from 205-315 after the autoplan review added 15 mandatory Step 0 amendments + 4 cherry-pick expansions). Red line: **350 hours**. If the project exceeds 350 hours without a clear path to v1 done criteria, stop and redesign. This isn't a forecast — it's a tripwire.

**v1 done criteria** (§OQ-6 v1-D1 through v1-D6):

1. Two-game cross-transfer (Minecraft `chop_logs` + Stardew `water_crops`) with zero Python delta
2. Phase C-0 passed ✓ (2026-04-11)
3. Three Design Rules enforced in CI on main ✓ (already green)
4. Scenario regression fixtures exist (Step 4+ scope)
5. Honest-effort commitment met or explicitly renegotiated
6. Sean uses it for himself — the "personal tool" validity check

**v2 trigger events** (need 2+ within 90 days post-v1):

1. **v2-T1** — third-game adapter in ≤8 hours of YAML-only work
2. **v2-T2** — skill library compounding (≥30% brain-call reduction on repeated tasks)
3. **v2-T3** — community fork OR external cite OR named researcher interest
4. **v2-T4** — Phase C-0 hard-case generalization to the third game's fixtures

If fewer than 2 trigger events fire, v1 stays as a personal tool permanently. That's an acceptable outcome.

---

## License

TBD (private repo during development). Public release is a v2-S1 trigger, not a v1 commitment.
