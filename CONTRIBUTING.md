# Contributing to GameMind

GameMind is in active development and welcomes contributions. During Phase C
(the initial implementation phase), the scope is narrow and focused. If you
want to contribute, read this first.

## Philosophy in one paragraph

GameMind is a universal game AI agent framework. It plays games via vision
and OS-level keyboard/mouse input only — no APIs, no mods, no memory reads.
The architecture is declarative over two axes (games via YAML adapter,
models via OpenAI-compatible backends). If a proposed change makes the
framework less general (specific to one game, one model, one OS), it is not
aligned with the project. The authoritative design document is
[`docs/final-design.md`](docs/final-design.md); read that before proposing
non-trivial changes.

## Before you propose anything non-trivial

1. Read `docs/final-design.md` (~480 lines). It locks in the six-layer
   architecture, the three design rules, and the hard boundaries of v1.
2. Read `phase-c-0/C0_CLOSEOUT.md`. It tells you which perception model was
   selected, why `t2_inventory` is non-blocking, and what D1-D5 fallbacks
   exist if Layer 1 capability degrades.
3. Read `CLAUDE.md` at the repo root. It contains the project-level
   development discipline.

## The three hard rules (CI-enforced)

These are not negotiable. A PR that violates any of them will be blocked
by CI:

1. **No hand-authored coordinates in the action layer.** Never write
   `mouse_move(280, 475)` with literal integers. All targets must come from
   runtime vision grounding. The action layer is for *how to click*, not
   *where to click*.
2. **No per-game Python in Layer 6 adapters.** Game adapters are pure YAML.
   No `if game == "minecraft"` branches anywhere in the codebase.
3. **Per-game prompts stay generic.** Prompt templates query adapter fields
   by name. Game knowledge lives in YAML data, not in prompt prose.
   Violation test: if removing the game name from a prompt would break
   task execution, the prompt is violating this rule.

## Development environment

- **Python 3.11** via [`uv`](https://docs.astral.sh/uv/) — not conda,
  not pyenv, not raw pip
- **Ollama 0.13+** native install (Windows or Linux)
- **Ruff** for linting and formatting (runs in CI)
- **GitHub Actions** for CI (`.github/workflows/ci.yml`)

To run the Phase C-0 probe locally:

```bash
cd phase-c-0
uv sync
uv run python -m probe.run --model qwen3-vl:8b-instruct-q4_K_M
```

Expected: `VERDICT: PASS` with T1 ≥ 50%, T3 ≥ 70%, T4 ≥ 70%, p90 latency
on blocking categories ≤ 1500 ms, JSON parse reliability ≥ 95%. T2 is
reported as informational. See `phase-c-0/C0_CLOSEOUT.md` for the rationale.

## How PRs work

1. **Branch from `main`** with a conventional name prefix:
   - `feat/` — new feature
   - `fix/` — bug fix
   - `chore/` — tooling, metadata, refactor-without-behavior-change
   - `docs/` — documentation only
   - `refactor/` — behavior-preserving code restructure
   - `test/` — test additions or changes
   - `perf/` — performance improvement
   - `ci/` — CI configuration changes

   Example: `feat/layer1-ollama-backend`, `fix/warmup-race-condition`.

2. **Commit messages use Conventional Commits**:
   `<type>(<scope>): <description>` — e.g.
   `feat(perception): add OllamaBackend with retry logic`.

3. **Keep PRs small** — aim for 100-500 lines. If a PR is bigger, the
   design is probably wrong and it should be split.

4. **Open the PR** with a description that has:
   - `## Summary` — 1-3 bullets on what changed and why
   - `## Test plan` — a checklist of what was verified

5. **CI must pass** before a PR can merge. CI currently enforces:
   - Ruff lint + format check on `phase-c-0/probe/`
   - Import smoke test for the probe modules
   - Three Design Rules enforcement (grep-based)

6. **Main branch is protected**. PRs merge via squash only (linear history
   required). Force-push to main is blocked; only the repo owner bypasses
   any rule, and only after explicit reasoning.

## AI agent contributors

Claude Code (and other AI agents) can make PRs directly in this repo.
AI-authored PRs should:

- Disclose the model in the PR description
- Follow the same review gates as human PRs (CI + `/review` + `/codex` when
  the change touches core perception / action / brain layers)
- Include a `Co-Authored-By:` trailer in commits

Sean retains approval authority on all merges to `main`.

## What we are NOT accepting right now

During Phase C, the scope is locked. We are not accepting:

- New game adapters beyond Minecraft (v2 scope)
- Alternative perception models beyond the selected `qwen3-vl:8b-instruct`
  without a probe regression run
- Refactors that dissolve the six-layer boundary
- Anti-cheat "creative interpretations" — if it would be flagged by
  Vanguard/EAC/etc., it is out of scope permanently
- Memory reads, mod integrations, game API hooks
- Rewrites of the final design without first filing an issue and
  justifying the v2 trigger

If you are unsure whether your idea fits, open an issue first and describe
the change in 3-5 sentences. The response will tell you whether to pursue
a PR or not.

## Reporting bugs

Open a GitHub issue with:

- What you were trying to do
- What happened instead
- Minimal reproduction steps
- Environment: OS, Python version, Ollama version, model used
- Relevant logs or probe report JSON

## Code of conduct

Be direct, be honest, be specific. Assume good faith. If a review comment
feels wrong, say so clearly and with evidence. The goal is working software,
not social comfort.
