# CLAUDE.md — gamemind project

Project-level instructions for Claude Code when working inside this repo.
Overrides nothing from the workspace-level `C:/Claude Code Beta/CLAUDE.md`
but adds project-specific guidance.

## Project state: Phase C-0 PASSED, Phase C build ready to start

- **Phase B done** — final design locked in (`docs/final-design.md`)
- **Phase C-0 gate passed** (2026-04-11) with `gemma4:26b-a4b-it-q4_K_M`
- **Phase C build** is the next step, gated on `/autoplan` being run against
  the final design doc **before any new code is written**

## Load order when starting a fresh Phase C session

1. Read `docs/final-design.md` (~480 lines) — **authoritative design**
2. Read `phase-c-0/C0_CLOSEOUT.md` — probe outcome + locked-in decisions
3. Read `docs/sean-approval-package.md` — Sean's 4 approval decisions

## Hard rules for all code in this repo

These come from the Three Design Rules in `docs/final-design.md`, enforced
by CI. If you are generating code, these are non-negotiable:

1. **NO hand-authored coordinates.** Never write `mouse_move(280, 475)`
   with literal integers in the action layer. All targets come from runtime
   vision grounding. CI greps for `mouse_move\(\d+\s*,\s*\d+\)` style.
2. **NO per-game Python in Layer 6.** Game adapters are pure YAML. No
   `if game == "minecraft"` branches anywhere in the codebase.
3. **Per-game prompts stay generic.** Prompt templates query adapter fields
   by name. Game knowledge lives in YAML, not prompt prose. Violation test:
   "if removing the game name from this prompt would break task execution,
   the prompt is violating this rule."

## Phase C kickoff discipline (CRITICAL)

**Do NOT jump into code** just because `phase-c-0/probe/` already exists and
works. Phase C is a 205-315 hour, 9-11 week build. The discipline is:

1. **First action**: invoke `/autoplan` with `docs/final-design.md` as the
   input. This runs `plan-ceo-review → plan-design-review → plan-eng-review
   → plan-devex-review` as a chain. Phase C-0 skipped this because it was
   empirical gate work; Phase C build cannot skip this.
2. **Second action**: invoke `/cso` for STRIDE/OWASP threat modeling.
   Anti-cheat safe input stack is Design Rule #3's origin; security review
   is not optional.
3. **Third action**: invoke `/codex` for cross-model adversarial review of
   the plan output. Phase B's adversarial-critic is gone; we need an
   independent outside voice before committing to implementation.
4. Only after all three pass do we start writing Phase C code.

**What `probe/` is for**: historical evidence + regression test. It is
**not** the Phase C codebase. Code reuse happens by **refactoring** not
**copy-pasting**:
- `probe/client.py` → refactored into `phase-c/perception/ollama_backend.py`
  as the first `LLMBackend` implementation
- `probe/tasks.py` prompts → migrated to `phase-c/adapters/minecraft_java.yaml`
- `probe/run.py` gate logic → repurposed as the regression test runner

## Git workflow (trunk-based, PR-gated)

- `main` is protected — no direct commits, no force push, CI must pass
- All work happens on short-lived feature branches: `feat/layer1-perception`,
  `chore/phase-c-prep`, `fix/warmup-race`, etc.
- PRs merge via `/ship` (auto-generated commit messages, CHANGELOG updates,
  PR description)
- PRs land via `/land-and-deploy` (waits for CI, merges, verifies)
- Weekly `/retro` to track velocity + code health trends

Small PRs are a discipline, not an enforcement rule. Aim for 100-500 lines
per PR. If a PR is bigger, the design is probably wrong.

## Dependencies

- **Python 3.11** via `uv` (not conda, not pyenv)
- **Ollama 0.13+** native Windows install (not WSL)
- **Claude API** via Max Plan allowance for Layer 3 brain
- **GitHub Actions** for CI (ubuntu-latest runner, Python 3.11)

## Things NOT in scope for v1

- Anti-cheat evasion (we stay safe, not hide)
- Multi-player / online games
- Non-Minecraft games (v2 goal only)
- Distributed / multi-machine deployment
- Cloud hosting (local-first, always)

## Language

All docs and comments in English. Conversations with Sean in Chinese.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- **Multi-week build kickoff (Phase C), "开工", "开始做" → invoke autoplan**
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Architecture review → invoke plan-eng-review
- Security / threat modeling / anti-cheat concerns → invoke cso
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health

The workspace-level `../CLAUDE.md` contains additional Phase-C-specific routing
discipline (bootstrap order, empirical gate work exceptions). Project-level rules
here are additive, not replacements.
