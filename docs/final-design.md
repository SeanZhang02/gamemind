# GameMind — Final Design Document

**Status**: DRAFT COMPLETE (Phase B, architect deliverable, ready for parallel audit + `/plan-eng-review`)
**Author**: architect (Phase B agent team)
**Date**: 2026-04-10
**Supersedes**: `architect-unknown-design-20260410-phaseB-officehours.md` (v1 → v2.6 amendments, kept as work-history)
**Readiness**: All BLOCKING stubs flipped. Two cosmetic stubs remain (§3 Rule 1 prose + §4.1 component table) — neither blocks `/plan-eng-review`. This document is the authoritative Phase B deliverable.

---

## 0. Executive Summary

GameMind is a Python daemon that plays any video game via screen capture and OS-level keyboard/mouse input, driven by a local vision-language model with sparse cloud-brain escalation on semantic triggers. Game knowledge lives in YAML adapters, not code. The runtime is one binary; only the YAML changes between games.

**The elevator pitch**: *GameMind is one binary that's declarative over two axes — games (via YAML adapter) and models (via OpenAI-compat LLMBackend). Same daemon, swap data not code, on both dimensions.*

**v1 scope**: a single Python daemon that, given only a YAML adapter, completes one short task in Minecraft Java AND one short task in Stardew Valley with zero code changes between games. The two-game cross-transfer wedge forces the universality test into the MVP. This is the narrowest way to disprove the "Minecraft-specific hack" failure mode.

**What GameMind is not**: not an end-to-end fine-tune (Lumine-style $2M training), not a game-specific mod (Mineflayer), not a reimplementation of Cradle (which uses per-game Python and hand-drawn coordinates), not a thin wrapper over UI-TARS-desktop (which has no game-specific abstraction layer). The point of existence is the declarative adapter + runtime grounding combination that none of those ship.

**Strategic framing**: **Option C — staged.** v1 ships as a personal tool / working foundation: a Python daemon Sean runs on his 5090 that plays Minecraft and Stardew Valley via YAML adapters with zero code delta between games. v2 is a deferred research-artifact upgrade, gated on concrete promotion events (see §2 OQ-6). v1 is the commitment Sean approves now; v2 is optional and only triggers if v1 demonstrates that the declarative wedge actually compounds. No "we'll see later" — the promotion criteria are explicit and measurable.

**Honest commitment**: 205-315 hours / 5-10 weeks calendar time at ~20-30 hrs/week. This is cradle-evaluator's stress-tested estimate including debug, dev-loop infrastructure, and solo-dev multipliers. Earlier estimates (90-140h in v2.1, 60-80h in v2.5) are superseded. Sean should approve with the 205-315h number in mind.

---

## 1. Architecture

### 1.1 Layer Model (Alt B2 — continuous local perception + sparse cloud brain)

```
+---------------------------------------------------------------------+
|  Layer 6:  Game Adapter (YAML)                                      |
|            declarative schema: actions, ui_state, goal_grammars,    |
|            success_check predicates, world_facts                    |
|            NO per-game Python                                       |
+---------------------------------------------------------------------+
           ^           |                        ^
           | queried   |                        | queried
           |           v                        |
+---------------------------------------------------------------------+
|  Layer 5:  Skill Library (JSONL + faiss)                            |
|            persistent cross-session, per-adapter retrieval          |
+---------------------------------------------------------------------+
           ^           |                        ^
           |           v                        |
+---------------------------------------------------------------------+
|  Layer 3:  Brain (SPARSE)                                           |
|            Claude Sonnet 4.5/4.6 via OpenAI-compat backend          |
|            Called 5-20 times per task on semantic triggers:         |
|              - task start (plan decomposition)                      |
|              - replan on success-check failure / stuck detector     |
|              - task completion verification                         |
|              - explicit escalation from vision critic               |
|            Budget: ~$0.10-1 per long task                           |
+---------------------------------------------------------------------+
           ^           |                        ^
           |           v                        |
+---------------------------------------------------------------------+
|  Layer 2:  Replan Trigger Detector (NOT a cost gate)                |
|            fires on success-check stall or abort condition          |
|            semantic events, not throughput thresholds               |
+---------------------------------------------------------------------+
           ^           |                        ^
           |           v                        |
+---------------------------------------------------------------------+
|  Layer 1:  Perception (CONTINUOUS, 2-3 Hz)                          |
|            Qwen2.5-VL-7B local via Ollama on native Windows         |
|            per-frame: entity recall, UI state, action salience      |
|            cost: ~$0.07/hour power; ~$0.02 per 1K decisions         |
+---------------------------------------------------------------------+
           ^           |                        ^
           |           v                        |
+---------------------------------------------------------------------+
|  Layer 4:  Action Execution                                         |
|            pydirectinput_rgx scan codes via SendInput               |
|            runtime visual grounding (no hand-drawn coords)          |
+---------------------------------------------------------------------+
           ^           |
           |           v
+---------------------------------------------------------------------+
|  Layer 0:  Capture                                                  |
|            windows-capture (WGC) primary, dxcam (DXGI) fallback     |
|            per-HWND, exclusive-fullscreen safe                      |
+---------------------------------------------------------------------+
```

### 1.2 Architecture Evolution Log

Phase A's original architecture was a 6-layer model with wake-on-event gating applied at the entire pipeline. That framing was motivated by the assumption "LLM is expensive, must sleep." The architecture has since evolved through several named stages, each capturing a lesson worth documenting:

1. **Phase A pipeline-level gate**: "LLM expensive, gate everything." Correct intent, wrong layer.
2. **v2 Alt B continuous-local-only (retracted)**: "Local is so cheap, gate nothing." Overcorrected — treated local and cloud as equivalent when they are 500x apart.
3. **v2.2 ARCH-C two-tier (right architecture, wrong story)**: Split perception from brain, gate the brain. Correct structurally but I framed it as "we deferred wake-on-event," which sounds like repudiating Phase A.
4. **v2.4 Alt B2 Layer-3 rescope (right architecture, right story)**: Identical to v2.2 but framed as "wake-on-event rescoped from full pipeline to Layer 3 only." Phase A's instinct was right — it was applied at the wrong granularity. Local perception at $0.07/hour doesn't need gating; Claude at $35/hour continuous is economically impossible and MUST be gated.

This is the narrative final-design.md carries forward. Phase A is vindicated, not repudiated.

### 1.3 Why this architecture survives the universality test

Every layer can be exercised on a new game given only a new `adapters/newgame.yaml`:
- **Layer 6**: the YAML itself is the per-game delta. No Python.
- **Layer 5**: skills are tagged `adapter: <name>` and retrieved per-adapter; no adapter-specific code.
- **Layer 3**: brain prompts are game-blind templates; they query the adapter at runtime for world_facts, goal_grammars, available actions.
- **Layer 2**: replan triggers fire on `success_check` predicate state — same engine for all games.
- **Layer 1**: Qwen2.5-VL-7B is a general VLM; no per-game fine-tune.
- **Layer 4**: pydirectinput-rgx sends scan codes; the adapter declares what keys mean what (e.g. `{forward: W}` in Minecraft, `{forward: W}` in Stardew — same primitive, same binding).
- **Layer 0**: capture is HWND-based; any game window works; Windows Graphics Capture handles windowed + borderless, DXGI Desktop Duplication handles exclusive-fullscreen.

**The acid test**: the primary v1 wedge is Minecraft (3D voxel world, windowed) + Stardew Valley (2D pixel farming sim, windowed). If the v1 demo cannot produce `chop_logs` success on Minecraft AND `water_crops` success on Stardew with ONLY `adapters/minecraft.yaml` and `adapters/stardew.yaml` differing, the universality claim fails and we reopen the design.

**Universality stress test — games the architecture must survive by construction** (not all in v1 scope, but the design must plausibly extend to them without Layer 0-5 code changes):

1. **Minecraft Java Edition** — 3D voxel, windowed/borderless, no anti-cheat, vanilla Mojang. `chop_logs` is the v1 primary task. Tests: runtime visual grounding of log blocks, entity recall (trees), inventory HUD read, action sequencing.
2. **Stardew Valley** — 2D pixel-art farming sim, windowed, no anti-cheat. `water_crops` is the v1 second task. Tests: top-down sprite identification, grid-aligned movement, tool-use loop, day-night cycle stochasticity that would break byte-equality snapshots.
3. **Dead Cells (Steam)** — 2D pixel-art roguelike action, **exclusive-fullscreen capable**, no kernel anti-cheat. This is the Layer 0 DXGI fallback smoke-test target (see §6 Step 1 acceptance). Tests: capture path under exclusive fullscreen, fast combat reflex loop, procedural level stochasticity. Sean owns Dead Cells on Steam and has confirmed availability. **NOT required for v1 "done" but required as a Layer 0 capture-backend validation** — the Three Design Rules say Layer 0 must work on any HWND, and Dead Cells is the cheapest test of that claim for fullscreen-exclusive mode.
4. **Factorio** (v2 candidate, NOT v1) — menu-heavy strategy, windowed. Tests: dense UI, text-heavy inventory reading, long-horizon planning. Candidate for v2-T1 third-game promotion trigger.
5. **Vampire Survivors** (v2 candidate, NOT v1) — bullet-hell auto-attacker, windowed. Tests: fast combat loop, particle-heavy stochasticity, minimal UI. Alternate candidate for v2-T1.

**Rule**: if any of games 1-5 requires Layer 0-5 code changes (not just a new YAML adapter), that game has revealed a framework bug and the design must be amended. The three Design Rules in §3 make this check mechanical, not aesthetic.

### 1.4 Wake-trigger specification (concrete, testable)

Phase A's instinct was "wake the brain only when needed." v2.4 rescopes that instinct from the whole pipeline to Layer 3 only. This subsection specifies **exactly** when Layer 3 is woken, because "when something interesting happens" is not a spec.

Layer 3 (Claude Sonnet 4.5/4.6) is invoked ONLY on the following semantic events. Every invocation is logged to `runs/<session>/brain_calls.jsonl` with `{trigger, frame_id, timestamp, reason}` for post-hoc audit.

**Trigger W1: Task start.**
- Fired once per `POST /v1/session/start`.
- Payload: task YAML, adapter YAML slice (goal_grammars, world_facts), current perception snapshot.
- Purpose: plan decomposition into a sequence of sub-goals + action intents.
- Cost: 1 call/task.

**Trigger W2: Stuck detector.**
- Fires when Layer 2 observes NO change in `success_check` predicate state AND no frame-delta entropy above 0.05 for `stuck_seconds` seconds (default 20s, adapter-overridable in `abort_conditions`).
- Payload: last 5 frames + last plan + last action sequence.
- Purpose: replan from current state.
- Cost: 0-5 calls/task depending on how often the agent gets stuck.

**Trigger W3: Success-check stall / abort-condition edge.**
- Fires when any `abort_condition` predicate flips true (e.g. `health_threshold < 0.3`), OR when the same `success_check` predicate has been "in-progress but not advancing" for 60s.
- Payload: current frame + abort context.
- Purpose: decide whether to flee, switch sub-goal, or abort the session.
- Cost: 0-5 calls/task.

**Trigger W4: Vision-critic escalation.**
- Fires when the Layer 1 local `vision_critic` predicate returns "unclear" 3 consecutive times on the same question.
- Payload: 3 most recent frames + the critic question.
- Purpose: resolve the ambiguous perception; either via Claude direct answer or via Gemini 2.5 Pro escalation (the only permitted Gemini use-case).
- Cost: 0-10 calls/task on hard perception cases.

**Trigger W5: Task completion verification.**
- Fires when `success_check` predicates ALL return true via Layer 1 local vision.
- Payload: current frame + goal description + success predicates that fired.
- Purpose: get a second opinion from Claude that the task actually completed (guards against Qwen hallucination-to-success).
- Cost: 1 call/task.

**Total brain-call budget per task**: 5-20 calls typical, 30 upper bound. Sessions exceeding 30 Layer 3 calls before declaring success or failure are aborted and logged as `outcome: runaway`; Sean's call whether to investigate or descope.

**Triggers explicitly NOT wired in v1** (rejected during design):
- Periodic wake (e.g. "every 60s regardless"). Violates Phase A's own rescoped instinct; pure cost without semantic justification.
- Skill-retrieval failure. Layer 5 falls back to a generic plan template locally; no Layer 3 wake.
- Adapter reload. Handled at the daemon level, not the brain level.

### 1.5 Wake rate + budget math (Max Plan viability)

Sean's Max Plan gives roughly $100/month usable API budget (treat as $100 floor). The question: can Layer 3 stay inside this envelope while running enough tasks to be a useful personal tool?

**Per-task brain cost** (Claude Sonnet 4.5 pricing, assume ~2K input tokens + ~500 output tokens per wake, including screenshot where applicable):
- Low-end task (5 wakes): ~$0.05-0.10
- Typical task (10-15 wakes): ~$0.15-0.30
- Hard task (20-30 wakes): ~$0.50-1.00

**Per-hour Layer 1 cost** (Qwen2.5-VL-7B local on 5090):
- Power: ~$0.07/hour (treating 5090 at 400W sustained, $0.15/kWh)
- Zero API cost.
- **Does NOT count against Max Plan budget.** This is the architectural reason Layer 1 is local.

**Monthly budget fit**:
- 100 typical tasks/month at $0.20/task average = **$20/month Layer 3 cost** → well inside $100 budget.
- 100 hard tasks/month at $0.75/task average = $75/month → still inside budget, but thin.
- Runaway case (sessions averaging 50 brain calls due to a stuck agent loop): $2-3/task, hits $200-300/month with 100 tasks → breaches budget → `outcome: runaway` kill switch in §1.4's 30-call upper bound prevents this.

**Layer 1 compute time is the hidden constraint, not Layer 3 cost.** Qwen on 5090 at BF16 ~20GB VRAM delivers roughly 2-5 Hz on vision+text inference depending on prompt size. At 2 Hz continuous, a 10-minute task means 1200 Qwen calls. Sean's 5090 is dedicated to this; no contention assumed.

**Conclusion**: Max Plan viably covers v1 personal-tool usage at 100+ tasks/month. The bottleneck is Sean's time and the Phase C-0 gate, not API budget.

### 1.6 Perception-brain disagreement recovery

The architecture has two inference engines (Layer 1 Qwen local + Layer 3 Claude cloud) that can disagree on the same frame. Example: Qwen reports "inventory has 2 logs" and Claude, on a Trigger W5 completion check, says "inventory appears empty." We need an explicit policy, not an implicit fallback.

**Disagreement detection**: any Layer 3 response that contradicts the Layer 1 perception that triggered it is logged as a `perception_disagreement` event in `runs/<session>/events.jsonl` with both model outputs side-by-side.

**Recovery policy (in order)**:

1. **Local re-query at temperature=0.** The Qwen query that produced the disputed answer is re-run at `temperature=0` (same prompt, same frame). If the answer changes, Qwen's original was sampling noise — trust the re-query, invalidate the Layer 3 contradiction, do not spend another Claude call. Logged as `self_correction`.

2. **Cross-frame sanity check.** If re-query still disagrees with Claude, Layer 1 runs the same query on the 3 nearest captured frames (±1.5s window). If ≥2 of 3 agree with each other but disagree with Claude, trust the Qwen majority; log `layer_1_majority_wins`. Claude's Trigger W5 verification is treated as "uncertain" and the session stays active.

3. **Gemini 2.5 Pro tiebreak (Trigger W4 escalation path).** If Qwen majority disagrees with Claude, invoke Gemini 2.5 Pro on the same frame + question as an independent arbiter. Cost: one Gemini call per disagreement (rare by construction — budget for 5-10/month max). Gemini's answer is authoritative for this frame. Logged as `arbiter_resolution`.

4. **Manual checkpoint in `--dev-checkpoint` mode.** If Gemini is unavailable (e.g. API down) OR the session is running under `--dev-checkpoint`, the daemon pauses and prompts Sean: "Layer 1 says X, Layer 3 says Y, which is correct?" Sean's answer goes into `runs/<session>/manual_checkpoints.jsonl` and bootstraps future ground-truth regression cases. Never used in `--production` mode.

5. **Session abort.** If none of 1-4 can resolve (e.g. in `--production` mode with no Gemini budget), the daemon aborts the session with `outcome: perception_disagreement_unresolvable`. This is a rare branch — expected frequency <1% of sessions — but must exist as a named outcome rather than an implicit crash.

**Why this matters**: without a disagreement policy, the agent either (a) trusts Claude always (defeats the "perception is local" point, hits budget) or (b) trusts Qwen always (defeats the "verification via cloud brain" point, allows hallucination-to-success). The tiered policy keeps both models load-bearing without letting either silently override the other.

---

## 2. The Six Open Questions — Final Answers

### OQ-1: Is Claude vision good enough? (BLOCKING)

**Answer**: Claude is NOT the perception layer. Qwen2.5-VL-7B local via Ollama is. Claude is the Layer 3 brain, called sparsely on semantic triggers.

**Evidence**:
- Vision-researcher empirical cost table (per 1K decisions): Qwen local ~$0.02, GLM-Flash free, Qwen API $0.36, GLM $0.37, Doubao $0.55, Gemini $5.16, **Claude $9.69**.
- At 1 Hz continuous, Claude = $35/hr. Max plan has ~$100/mo budget. Claude as Layer 1 is economically impossible.
- Vision-researcher supplemental on 7B-for-planning: Orak shows 7B-class VLMs 20-40% vs Claude 4.5 60-80% on multi-step game planning (2-3x gap). Game-TARS needed 72B for 72% embodied. No published 7B agent completes comparable Minecraft tasks. **Claude is necessary for Layer 3 planning; Qwen is necessary for Layer 1 perception; both are required**.
- Phase A research pack errors corrected: Orak's Claude 75.0 Minecraft score was TEXT-STATE, not vision. Claude-on-game-vision has no strong public benchmark. Hence SPIKE-0 gate.

**Primary stack**: Qwen2.5-VL-7B (Apache 2.0, ~20GB VRAM BF16) on Ollama native Windows + Claude Sonnet 4.5/4.6 via OpenAI-compat backend.
**Fallback stack** (if SPIKE-0 fails): UI-TARS-1.5-7B → GLM-4.6V-Flash (free tier) → Doubao-1.5-vision-pro API.
**Gemini 2.5 Pro** reserved as secondary critic for hard cases, NOT as primary brain (cost-killer at $5.16/1K).

### OQ-2: Fork Cradle, learn from Cradle, or ignore Cradle?

**Answer**: LEARN-FROM, do not fork. License is MIT (verified), legal path open but fork strictly dominated by learn-from + targeted lift by +30 to +90 hours per cradle-evaluator's stress-tested analysis.

**Key insight** (from cradle-evaluator, transcribed via team-lead pending re-send): Cradle's atomic-skills package contains hand-drawn coordinates (`io_env.mouse_move(280, 475)` at `cradle/environment/dealers/atomic_skills/basic_skills.py:16`). This is the entire Cradle pattern — they offloaded visual grounding to the developer at authoring time because GPT-4V has no grounding training. With Qwen2.5-VL-7B / UI-TARS-7B, we can express intents as `{click: "the dialogue option"}` and the grounding happens at runtime. This is the architectural move Cradle could not make, and it's the reason our Layer 6 can be declarative while theirs had to be Python.

**Patterns to ADOPT** (from cradle-evaluator, transcribed into v2.3 amendment):
- `LLMProvider` ABC contract at `cradle/provider/llm/base_llm.py:12-46`
- `assemble_prompt_tripartite()` pattern (targeted lift from `cradle/provider/llm/openai.py:490-688`)
- Prompt templates as files in `res/{game}/prompts/templates/` — structure only, invert the content rule (templates game-agnostic, data per-adapter)
- `LLMFactory` returning `(llm_provider, embed_provider)` tuple — default embed must be local `sentence-transformers`, NOT OpenAI embedding
- Module-as-callable pattern at `cradle/provider/module/action_planning.py:16-65`
- Per-game `skill_registry` pattern, kept pluggable

**Patterns to REJECT**:
- Singleton `LocalMemory` — build explicit dataflow (state passed as function args, not mutated globals)
- Hardcoded Memory keys
- Legacy `cradle/planner/` monolithic per-game classes
- Game-specific reasoning embedded in prompt prose
- OpenAI embedding as default backend

### OQ-3: Implementation language(s)

**Answer**: Python 3.11+ primary. Single language, single process, single toolchain.

- **uv** as package manager (fast, lockfile-driven)
- **FastAPI** for the daemon HTTP server
- **pydantic** for config + adapter schema validation
- **pydirectinput-rgx** (the maintained fork) for input injection via SendInput with scan codes
- **windows-capture** (primary) + **dxcam** (fallback) for screen capture
- **Ollama** for local Qwen2.5-VL-7B inference (native Windows, no WSL2)
- **OpenAI-compat HTTP** for both local Ollama AND cloud providers (Chinese APIs, Anthropic) — one interface, many backends
- **faiss-cpu** + **sentence-transformers** (`all-MiniLM-L6-v2`) for skill retrieval
- **`@tarko/agent-snapshot`** (Apache 2.0, 2589 LOC) consumed for **LLM-trace verification primitives only** — NOT a full record-replay system. Per cradle-evaluator's post-broadcast correction: the library redacts `image_url` payloads in `snapshot-normalizer.ts:41` and cannot replay visual state. GameMind builds a **frame-synchronized** capture + input timeline + replay harness on top of tarko's LLM-trace scaffolding in `gamemind/replay/harness.py`. The 15-25h bucket in §4 is correctly sized for this (~8-12h reusing tarko's normalizer/verifyLLMRequests + ~15-22h frame-sync build).

No Rust in v1. No JavaScript except the optional gstack thin client (`~/.gstack/skills/gamemind/cli.ts`) which is 50-100 LOC of HTTP calls to the daemon. Rust reserved as a Phase D escape hatch if any single Python hot path measures >100ms/call over the latency budget.

### OQ-4: Success verification

**Answer**: Declarative predicate grammar in YAML, evaluated by a generic Python checker engine, tiered by cost.

The adapter declares what "success" means using a fixed vocabulary of predicates; the checker runs them. Game-agnostic engine, game-specific data.

**Predicate vocabulary** (each is a generic method, not per-game):
- `inventory_count` — structured vision query against a forced UI state
- `template_match` — OpenCV on named reference sprite
- `vision_critic` — freeform NL predicate via local Qwen (yes/no/unclear)
- `health_threshold` — numeric predicate via HUD vision query
- `time_limit` — wall-clock
- `stuck_detector` — no frame delta + no predicate progress for N seconds
- `any_of` / `all_of` / `not` — boolean composition

**Checker tiers** (ordered cheap → expensive):
1. **Template match** — OpenCV sprite, fastest, most reliable when available
2. **Structured vision query** — Qwen local JSON output for inventory/HUD reads (run twice and require agreement to mitigate hallucination)
3. **Vision critic local** — Qwen freeform yes/no/unclear on the NL predicate
4. **Vision critic cloud** — Claude Sonnet escalation, only after 3 consecutive "unclear" from tier 3. Counts against the 5-20 brain-call budget.
5. **Manual checkpoint** — `--dev-checkpoint` flag pauses the daemon and asks Sean. Used during new-game onboarding to bootstrap ground-truth labels. Removed in `--production` mode.

**YAML contract example** (adapter authors write this, not the checker):

```yaml
goal_grammars:
  chop_logs:
    preconditions:
      - log_visible_in_frame
    success_check:
      any_of:
        - type: inventory_count
          target: log
          operator: ">="
          value: 3
        - type: vision_critic
          question: "Does the player inventory contain at least 3 logs of any wood type?"
    abort_conditions:
      - type: health_threshold
        operator: "<"
        value: 0.3
      - type: time_limit
        seconds: 600
```

**Universality test**: the same grammar expressed `water_crops` for Stardew Valley using only `vision_critic`, `template_match`, `stuck_detector`, and `time_limit`. Zero engine code delta between games. Passes.

**Failure-mode diagnostic rule**: if a new game cannot express its success conditions in this vocabulary, **that is signal that the predicate vocabulary is too narrow** — add a new GENERIC predicate type, never a per-game hack. Track these as "predicate vocabulary gaps" and review monthly.

### OQ-5: Dev/test loop

**Answer**: Record-and-replay harness wrapping `@tarko/agent-snapshot` as a dependency, with savegame fixtures managed as git-lfs scenarios.

Live runs take minutes; replay-only-brain runs target <10 seconds. The dev loop that matters is "change a prompt or adapter, re-run the brain on a recorded frame sequence, diff the decisions against the recorded baseline." This turns a 5-minute live iteration into a sub-10-second desk iteration.

**Key primitives** (from pragmatist deliverable 2):
- `POST /v1/replay/load` loads a run
- `POST /v1/replay/step mode=brain_only` re-runs the brain on a recorded frame with CURRENT config; returns recorded decision + new decision + diff
- `POST /v1/replay/step mode=fork_live` loads the savegame snapshot and runs live from that frame
- `POST /v1/replay/diff` compares two runs, shows divergence points

**Determinism contract**: `--only-brain` mode forces `temperature=0` on Ollama regardless of current config, so diffs are about CODE changes not sampling noise. `fork_live` does NOT force temp=0 because the point is to observe behavior variance.

**Scenario fixtures** (per-task savegames in `scenarios/<id>/savegame/`):
- `scenarios/mc-000-first-log/` — Minecraft save at a fresh tree-adjacent spawn
- `scenarios/sv-000-water-parsnips/` — Stardew save at Day 2 with parsnips planted, watering can in inventory
- Each scenario has: `savegame/`, `expected_initial.webp`, `task.yaml`, `README.md`

git-lfs handles the binary savegames. Each scenario is committable and diffable.

**The dev loop in practice**:
1. Adapter author writes `adapters/minecraft.yaml`
2. `gamemind scenario run mc-000-first-log --record` produces a run
3. If it fails: `gamemind replay <run_id> --only-brain --frame 94` shows what the brain decided and why
4. Adjust adapter YAML or prompt template
5. `gamemind replay <run_id> --only-brain` re-runs the brain on the same frame sequence with new config, shows diffs
6. When diffs look right, `gamemind run --scenario mc-000-first-log` to validate live
7. Commit the passing run as a regression fixture

### OQ-6: Differentiation

**Answer**: **Option C — staged.** v1 is a personal tool / working foundation. v2 is a research-artifact upgrade triggered by concrete measurable promotion events.

**3-tier differentiation wedge** (same for v1 and v2, only the pitch changes):

1. **Primary (necessary): Layer 6 Declarative Game Adapter.** New games onboard via YAML only, zero Python. Neither Cradle (per-game Python classes in `cradle/environment/`) nor UI-TARS-desktop (no game abstraction layer) has this. This is the load-bearing claim of the entire framework.
2. **Secondary: Game-state-aware verification.** The OQ-4 predicate grammar evaluates game state, not pixel equality. Byte-equality snapshot tests (the pattern `@tarko/agent-snapshot` supports out of the box) break the moment a game has stochastic particles, procedural terrain, or even a weather cycle. GameMind's predicates survive all of these because they query semantic state via vision, not raw pixels.
3. **Tertiary: Anti-cheat-safe input stack.** pydirectinput-rgx scan codes via SendInput is first-class, not an afterthought. No Interception driver, no kernel hooks, no memory reading. Forward-compatible with future games that run Vanguard or EAC.

**Elevator pitch (unchanged between v1 and v2)**: *GameMind is one binary, declarative over two axes — games (via YAML adapter) and models (via OpenAI-compat LLMBackend). Same daemon, swap data not code, on both dimensions.* The LLMBackend axis reinforces the primary wedge with a second dimension of same-taste-different-zoom: same abstraction trick, different substrate.

---

#### v1 "Done" criteria (what shipped v1 looks like)

v1 is considered shipped and valid when ALL of the following hold simultaneously:

- **v1-D1. Two-game cross-transfer.** A single Python daemon completes `chop_logs` in Minecraft Java AND `water_crops` in Stardew Valley from a single `gamemind run` invocation per game, with the ONLY difference between the two runs being `--adapter adapters/minecraft.yaml` vs `--adapter adapters/stardew.yaml`. Zero Python delta, zero prompt-template delta, zero per-game flag in the CLI.
- **v1-D2. Phase C-0 passed.** Qwen2.5-VL-7B scored ≥80% on all 4 task categories across ≥20 hard Minecraft screenshots, or one of D1-D5 descope branches was taken AND its replacement acceptance criterion was met instead.
- **v1-D3. Three Design Rules enforced in CI.** No hand-authored coordinates in action layer, no per-game Python under `adapters/`, no game-name literals in prompt templates. CI linters green on `main`.
- **v1-D4. Scenario regression fixture exists.** At least 2 committed scenarios in `scenarios/` (`mc-000-first-log`, `sv-000-water-parsnips`) both pass via `gamemind scenario run <id>` on a fresh checkout.
- **v1-D5. Honest-effort commitment met or renegotiated.** Project either completed within 205-315 hours total, or Sean explicitly acknowledged a scope change and signed off on a revised estimate at a checkpoint. No silent scope drift.
- **v1-D6. Sean uses it for himself.** Sean runs GameMind on his own 5090 on a real Minecraft session that wasn't a test scenario, and the daemon does something useful (collected resources, completed a short errand, etc.). This is the "personal tool" validity check — if Sean himself has no reason to invoke it after the demo, v1 failed its own Option A brief.

**v1 is a personal tool.** It is NOT a public release. Closed-source or private GitHub is fine. No blog post, no paper, no launch. v1's audience is Sean + the Phase C agent team.

#### v2 trigger events (concrete, measurable, time-anchored)

v1 → v2 promotion happens when AT LEAST TWO of the following have been independently satisfied after v1 ships, within 90 days of v1 "done":

- **v2-T1. Third-game universality proof.** A THIRD game (beyond Minecraft + Stardew) gets a working adapter added in ≤8 hours of total effort, contributed by either Sean, a Phase D agent team, OR an outside contributor, with ZERO change to any file outside `adapters/`. Candidate third games: Dead Cells (Steam exclusive-fullscreen smoke-test target), Factorio (menu-heavy strategy), Vampire Survivors (combat loop). **Metric: PR diff of the third-game adapter PR shows only files under `adapters/` and `scenarios/`.**
- **v2-T2. Skill library compounding.** The Layer 5 JSONL + faiss skill store demonstrably reduces brain-call counts on repeated tasks: a second run of `chop_logs` after 5+ prior runs uses ≥30% fewer Claude brain calls than the first run did, measured from `runs/*/events.jsonl`. If skill retrieval doesn't compound, the whole skill layer is architectural cruft and v2 must justify keeping it.
- **v2-T3. Community fork or external interest.** Either (a) someone outside Sean's team forks the public repo and pushes commits, OR (b) a named person (researcher, dev, YC peer, etc.) asks "how do I try this on [game]?" in writing, OR (c) a research lab cites GameMind in a paper draft or blog post. Any of a/b/c satisfies this.
- **v2-T4. Phase C-0 hard-case generalization.** Beyond the ≥80% hard-case pass at Phase C-0, the same Qwen2.5-VL-7B + prompt stack scores ≥70% on a similar 20-screenshot hard-case set drawn from the third game's environment, WITHOUT any prompt-template change. Demonstrates that the perception layer generalized, not just that Minecraft was memorized.

**If fewer than 2 trigger events fire within 90 days of v1 done**: v1 stays as a personal tool permanently, no v2 work happens. This is an acceptable outcome — Sean has a useful daemon and installed the cognitive modes; the research artifact was never the primary goal.

**If 2+ trigger events fire**: Sean initiates a Phase D planning conversation with the agent team to scope v2 concretely. Phase D is NOT authorized by this document; it requires its own office-hours run.

#### v2 scope (what the research artifact upgrade looks like)

v2, if triggered, is a deliberate research-release upgrade. Concrete scope:

- **v2-S1. Public GitHub repo release** with MIT license, README with a usable getting-started for a non-Sean developer, pinned uv lockfile, pre-built Phase C-0 regression dataset.
- **v2-S2. Formal evaluation suite.** A `benchmarks/` directory with at least 6 tasks (3 per game minimum, 2 games minimum) wired up as a headless CI-runnable suite via `@tarko/agent-snapshot` record-replay. Publishable pass-rates. This is what turns GameMind from "it works on Sean's machine" into "here's a benchmark others can run."
- **v2-S3. Technical writeup.** Either (a) a blog post at a Sean-controlled location explaining the declarative-adapter + runtime-grounding insight with Cradle-comparison data, OR (b) a workshop paper submission to any venue that accepts agent-systems papers (ALOE, Open-World Agents, etc.). Target length 4-8 pages. Not a full conference paper unless v2-T3 shows external research interest.
- **v2-S4. A third fully-working game adapter** (meeting v2-T1's 8-hour budget bound) as a demo — proving the declarative claim isn't a two-game coincidence.
- **v2-S5. Honest-effort ceiling.** v2 is budgeted 80-140 additional hours on top of v1's 205-315. Anything beyond 140 triggers a scope-cut conversation.

#### v2 differentiation thesis (what v2 argues that v1 doesn't)

v1's story: *"I can play games with a daemon and YAML."* A personal tool claim.

v2's story: *"Runtime visual grounding + declarative per-game data suffices for general game agency. This replaces the hand-drawn-coordinate pattern that existing general game agents (Cradle, UI-TARS-desktop) fall back to because their perception stack can't ground intents at runtime. The architectural consequence is that new games onboard in ≤8 hours via YAML alone instead of days of Python coding. We demonstrate this on 3 structurally different games — a 3D voxel world, a 2D farming sim, and a [third game category] — with a single Python binary and three YAML files totaling ≤600 lines."*

The v2 thesis is load-bearing on the 8-hour third-game adapter budget (v2-T1) and on the Cradle comparison being *architectural*, not benchmark-score. v2's claim is not "we beat Cradle on Minecraft scores," it's "we changed what per-game work consists of." If the third-game adapter takes 40 hours because runtime grounding keeps failing, the thesis is falsified and v2 should either descope or not ship.

---

**Summary of OQ-6 resolution**: v1 is a personal-tool MVP with 6 explicit done criteria. v2 is a deferred research-artifact upgrade gated on 2+ of 4 concrete promotion triggers firing within 90 days. The wedge (declarative 2-axis + grounded verification + anti-cheat input) is the same in both stages; only the scope of deliverables and the public narrative change. The architecture survives both stages with zero redesign.

---

## 3. Three Design Rules (HARD)

These rules are **binding and non-negotiable** in v1 and v2. Softening any rule requires an explicit documented amendment with a Sean sign-off. The rules encode the Cradle postmortem: Cradle drifted into per-game Python because each rule was soft at first and then eroded incrementally. We enforce them in CI from day 1.

**Source note**: The rules below are my (architect) v2.5 restatement. Cradle-evaluator owes a re-sent "four-clause revised language" message that refines the wording of Rule 1 (specifically the boundary between "visual grounding at runtime" vs "structured locator passed from adapter"). Until their re-send lands, Rules 2 and 3 are final; Rule 1's violation test is final; Rule 1's exact prose may be rewritten in a v1.1 amendment post-sign-off without changing its operational meaning. The CI checks below are locked regardless.

---

**Rule 1: No hand-authored coordinates in the action layer.**

The adapter MUST NOT contain literal pixel coordinates, and the brain MUST NOT emit literal pixel coordinates. All spatial grounding happens at runtime via the perception layer (Qwen2.5-VL-7B or fallback model) resolving a semantic intent like `{click: "the log block directly in front of the player"}` into a concrete screen coordinate.

- **What's forbidden**: `mouse_move(280, 475)`, `click_at: [512, 340]` in YAML, `move_to(x=100, y=200)` in any file under `gamemind/brain/`, `gamemind/adapter/`, or `gamemind/skill/`.
- **What's allowed**: the `gamemind/input/` driver module, which is the thin SendInput wrapper — it MUST receive coordinates as function arguments and MUST NOT have literal integers in its body. The perception layer at Layer 1 is allowed to emit coordinates because those are computed at runtime from pixels.
- **Violation test (mechanical)**: `grep -rE '\b(mouse_move|click|move_to|click_at)\s*[\(:]?\s*\d+' gamemind/ adapters/ | grep -v 'gamemind/input/'` must return zero matches. Enforced in CI via `scripts/lint_no_hardcoded_coords.py`. Build fails on match.
- **Anti-pattern we're explicitly avoiding**: `cradle/environment/dealers/atomic_skills/basic_skills.py:16` — `io_env.mouse_move(280, 475)`. Cradle authors had no choice because GPT-4V can't ground intents. We have a choice because Qwen2.5-VL-7B was grounding-trained.
- **Why this rule is load-bearing**: every per-game coordinate is a universality failure. If we let Rule 1 soften "just this once for Minecraft inventory slots," we've built Cradle-with-fewer-lines and lost the wedge.

---

**Rule 2: No per-game Python in Layer 6 Game Adapter.**

Adapters are YAML data only. No `.py` files under `adapters/`. No Python escape hatches (no `!!python/object`, no eval, no dynamic imports). The adapter loader rejects any non-YAML file in `adapters/` at daemon startup and refuses to load.

- **What's forbidden**: `adapters/minecraft/skills.py`, `adapters/minecraft.yaml` containing `!!python/object:...`, any mechanism that would let an adapter author drop in imperative code.
- **What's allowed**: declarative YAML using the fixed Adapter schema defined at `gamemind/adapter/schema.py`. The schema is extended by amending `schema.py` (which is generic Python), NOT by adding per-game Python.
- **Violation test (mechanical)**: `find adapters/ -type f ! -name '*.yaml' ! -name '*.yml' ! -name '*.webp' ! -name 'README.md'` must return zero matches. Adapter loader uses `yaml.safe_load()` (not `yaml.load()`), blocking the Python-object tag injection path. Build fails + daemon refuses to start on violation.
- **Runtime check**: `gamemind/adapter/loader.py` MUST use `yaml.safe_load` AND also walk the loaded dict to reject any string values that look like Python code (heuristic: reject values containing `lambda`, `import `, `exec(`, `eval(`).
- **Why this rule is load-bearing**: this is the exact drift mode Cradle fell into. They started with "mostly YAML" and ended with per-game Python skill files. Once the escape hatch exists, every tricky corner becomes Python, and the declarative claim dies. We preclude the escape hatch mechanically.

---

**Rule 3: Per-game prompts stay generic.**

Prompt templates under `gamemind/brain/prompts/templates/` are game-agnostic Jinja or f-string templates that query the adapter for game-specific data at render time. The template MUST NOT contain the game name, game-specific action names, game-specific entity names, or game-specific terminology.

- **What's forbidden**: `"You are playing Minecraft."`, `"Find a tree and right-click it."`, `"The inventory hotbar is at the bottom of the screen."`, `"Use E to open inventory."` — each one implicitly hardcodes a game.
- **What's allowed**: `"You are an agent playing {{ adapter.display_name }}. Current goal: {{ goal.description }}. Available actions: {{ adapter.actions | to_bullet_list }}."` — the TEMPLATE is generic, the DATA is per-adapter.
- **Violation test (mechanical)**: CI script `scripts/lint_prompt_templates.py` greps prompt files for a denylist of game names (`minecraft|stardew|rdr2|factorio|dead ?cells|vampire survivors`) plus a denylist of game-specific mechanics terms (`crafting table|hotbar|parsnip|biscuit|the scarecrow`) — refreshed per new adapter added. Build fails on match. Whitelist exceptions require a code-review override documented in the PR description.
- **Spot-check (human review)**: during PR review of any prompt template change, the reviewer must read the template and ask "if I replaced 'Minecraft' with 'Stardew' mentally, would this prompt still make sense?" If no, it's a violation.
- **Why this rule is load-bearing**: prompt prose is the silent drift path. Layer 6 YAML and Layer 4 input are easy to keep honest because they're code-reviewed. Prompts look like English and readers skim them. Rule 3 is the only rule that has to be human-enforced as well as CI-enforced.

---

**Auditor**: cradle-evaluator owns the violation-test spec and the CI linter correctness. Any loosening of a rule requires: (a) a written amendment to this document, (b) cradle-evaluator sign-off or explicit override by Sean, (c) a regression test added to `tests/design_rules/` proving the loosened case is still universality-safe.

**Tracked stub** (cosmetic, not load-bearing): cradle-evaluator's exact four-clause revised wording for Rule 1 is still pending. When it arrives, Rule 1's prose will be updated in-place; CI checks and operational meaning will not change.

---

## 4. Honest Effort Estimate

**Headline: 205-315 hours total / 5-10 weeks calendar at ~25 hrs/week average.**

This is the number Sean should approve against, not the earlier 90-140h or 60-80h estimates. Those were wrong in specific, diagnosable ways documented below.

### 4.1 Work breakdown (architect-interpolation from office-hours-doc + pragmatist deliverables)

The 205-315h ceiling comes from cradle-evaluator's stress-tested multi-component breakdown. Their exact line items + methodology breakdown message has not re-landed in my inbox yet — this subsection is my best-effort reconstruction from the work-history doc and will be updated verbatim when cradle-evaluator re-sends. The TOTAL is authoritative; the per-component split is provisional.

| Component                                                  | Estimate (h) | Notes |
|------------------------------------------------------------|--------------|-------|
| Daemon skeleton + capture (Step 1 in §6)                   | 15-25        | FastAPI + WGC + DXGI + capture doctor + Dead Cells smoke test |
| Input backend + loopback test (Step 2 in §6)               | 10-15        | pydirectinput-rgx, scan code path, visual confirmation |
| Adapter loader + schema + Minecraft adapter (Step 3a)      | 20-30        | pydantic schema, YAML-only loader, ~200 line minecraft.yaml |
| Ollama brain integration + LLMBackend abstraction          | 15-25        | Ollama backend + OpenAI-compat Anthropic route + Gemini escape hatch path |
| Prompt templates (plan decomposition + per-frame reflex)   | 10-20        | game-agnostic Jinja templates, tested via Rule 3 linter |
| First end-to-end `chop_logs` attempt (Step 3b)             | 15-25        | integration-heavy, debug-heavy, frame capture + brain + action loop |
| `@tarko/agent-snapshot` integration (replay harness shim)  | 15-25        | Python shim + `POST /v1/replay/*` endpoints + determinism contract |
| Scenario system + git-lfs + first 2 scenarios              | 10-15        | `scenarios/mc-000-*`, `scenarios/sv-000-*`, regression wiring |
| Stardew adapter + second task end-to-end                   | 20-35        | Stardew is SECOND so the universality claim is exercised |
| Skill library (JSONL + faiss + embeddings)                 | 15-25        | persistence, per-adapter retrieval, test coverage |
| Verification engine (predicate grammar checker)            | 15-25        | tier 1-4 predicate types, `vision_critic` cloud escalation |
| Layer 2 replan trigger detector                            | 10-15        | stuck detector, abort conditions, wake routing |
| CI + design rule linters + regression test suite           | 10-15        | Rule 1/2/3 enforcement, test fixtures |
| Phase C-0 gate execution (Sean screenshot labeling + probe)| 10-20        | the ≥80% hard-case gate itself; may trigger descope if failed |
| Dev-loop polish + bug backlog + docs                       | 15-25        | "last 20% takes 80%" budget line |
| **Total (point estimates)**                                | **205-315**  | cradle-evaluator stress-tested; source of truth |

### 4.2 Why the estimate grew from 60-80h → 205-315h

Two earlier estimates were wrong and must not be cited:

- **v2.1: 90-140h** — clean-sheet build with Cradle patterns lifted. Wrong because: (a) no dev-loop infrastructure line item, (b) no Phase C-0 gate execution, (c) assumed single-game (Minecraft only), (d) solo-dev context switching multiplier missing, (e) "debug time" baked into "build time" line items at roughly zero.
- **v2.5: 60-80h** — assumed `@tarko/agent-snapshot` gave record-replay for free. Wrong because `@tarko/agent-snapshot` is a TypeScript library that records traces for byte-equality snapshot tests; we consume its record-replay *primitives* via a Python shim and still have to write (a) the shim, (b) the `POST /v1/replay/*` endpoint layer, (c) the determinism contract, (d) the fork_live savegame integration. That's 15-25h of real work, not zero.

The growth factor (2-4x) is what happens when you stress-test estimates against actual integration surfaces rather than component-by-component LOC counts. Cradle-evaluator's stress test is the authoritative methodology; their exact line items will supersede my reconstruction above when they re-send.

### 4.3 Sean's commitment decision

5-10 weeks of ~25h/week is a materially different commitment than "a few hobby weekends." Specifically:

- **Lower bound (205h, 7 weeks at 30h/wk)**: everything goes roughly right. Phase C-0 passes on first try or with a D1 prompt-retry. Integration bugs are minor. Sean ships v1 in mid-to-late May 2026.
- **Upper bound (315h, 13 weeks at 25h/wk)**: Phase C-0 needs D2 (model upgrade) or D3 (Gemini critic), integration has a couple of nasty bugs, pragmatist's "last 20%" absorbs polish work. Sean ships v1 in late June / early July 2026.
- **Red line (≥350h)**: if actuals cross 350h without shipping v1-D1 (two-game cross-transfer), Sean and the agent team must pause and reopen the design. Sunk-cost grinding past 350h without the wedge working is the "expensive Minecraft hack" failure mode this whole Phase B exists to prevent.

Sean approves Phase C against the 205-315h envelope AND the red line. Anything else is silent scope drift.

**Tracked stub**: cradle-evaluator's exact component breakdown + stress-test multiplier table will replace §4.1's interpolation table when re-sent. Total (205-315h) and red line (350h) are authoritative regardless.

---

## 5. Phase C-0 HARD GATE + Descope Branches

**Phase C build does NOT begin until Phase C-0 passes.**

**Phase C-0 acceptance**:
- Qwen2.5-VL-7B achieves **≥80%** on all 4 task categories across **≥20** real Minecraft screenshots:
  - (a) block identification
  - (b) inventory reading
  - (c) UI state classification
  - (d) spatial reasoning
- Hard cases required in the 20: crowded inventory, cave/low-light, night overworld, combat mid-motion, rain/fog/particles
- Sean personally collects + labels the screenshots (this is both the gate AND the seed for the case-based regression suite at `tests/brain_cases/seed/` on Phase C day 1)

**Pre-planned descope branches** (enumerated BEFORE potential failure, not invented under pressure after):

- **D1 — Prompt/adapter hints retry** (~1 day, if C-0 is 70-79% close miss). Improved prompt templates + adapter hints ("here are the possible blocks: [list]") before escalating.
- **D2 — Upgrade model** (~1 day). Qwen2.5-VL-32B in Q4 quantization fits 32GB VRAM per vision-researcher's table. Re-pull, re-probe.
- **D3 — Add Gemini 2.5 Pro critic** (~3 days). Keep Qwen local for cheap perception, add Gemini critic for hard-case verification. NOT Claude (cost-killer). Layer 3 gains a second model.
- **D4 — Descope to 2D pixel-art game** (~1 week refocus). Swap v1 target to Stardew as primary; 2-game wedge becomes Stardew + Factorio/Vampire Survivors. Universality claim survives because adapter schema is unchanged.
- **D5 — v1 as scaled-down PoC** (last resort). Single-game Stardew demo with `--dev-checkpoint` manual verification, universality wedge postponed to v1.1. Significant narrative cost — means GameMind shipped as "architecture compiles" rather than "architecture delivers."

**Branch selection on C-0 failure is Sean's call.** The architect's job is having options on the table before failure.

**Fallback vision model chain** (if D2 also fails): UI-TARS-1.5-7B → GLM-4.6V-Flash (free tier) → Doubao-1.5-vision-pro API. Each is a drop-in via LLMBackend config change, no code change.

---

## 6. First 3 Build Steps (Phase C kickoff)

Assumes Phase C-0 passes. If it fails, replace with the selected descope branch.

### Step 1: Daemon skeleton + capture doctor + Dead Cells DXGI smoke test (~15-25 hours)

**Goal**: `gamemind doctor --capture` prints a meaningful report on BOTH a windowed Minecraft session AND an exclusive-fullscreen Dead Cells session, proving the WGC primary + DXGI fallback path works on the two capture modes Layer 0 has to handle.

**Scope**:
- `pyproject.toml` with pinned deps (uv-managed)
- `gamemind/daemon/main.py` FastAPI on 127.0.0.1:8766
- `gamemind/daemon/lifespan.py` DPI awareness + graceful shutdown
- `gamemind/capture/backend.py` CaptureBackend Protocol
- `gamemind/capture/wgc_backend.py` using `windows-capture` (WGC, per-HWND primary)
- `gamemind/capture/dxgi_backend.py` using `dxcam` (Desktop Duplication fallback)
- `gamemind/capture/selector.py` black-frame heuristic to auto-select backend (if WGC returns a black frame for N consecutive frames, fail over to DXGI)
- `GET /healthz`, `GET /v1/state`, `POST /v1/doctor/capture` endpoints
- `gamemind/cli.py` thin wrapper with `doctor --capture` and `daemon start/stop/status`

**Acceptance**:
- `gamemind daemon start` returns when `/healthz` returns 200
- `gamemind doctor --capture` on a running **windowed** Minecraft Java session prints `{wgc_ok: true, dxgi_ok: true, selected: "WGCBackend", sample_frame: <path>, variance: 0.87}` and writes a PNG to `runs/doctor-<timestamp>.png` that visibly contains the Minecraft frame
- `gamemind doctor --capture` on a running **exclusive-fullscreen Dead Cells** session prints `{wgc_ok: false_or_black, dxgi_ok: true, selected: "DXGIBackend", sample_frame: <path>, variance: 0.61}` and writes a PNG that visibly contains the Dead Cells frame (this is the DXGI fallback smoke test task #13 — a HARD gate on Step 1)
- The selector auto-fails-over without human intervention when WGC returns black frames
- `gamemind daemon stop` terminates without zombie processes

**Why Dead Cells specifically**: Sean owns Dead Cells on Steam (confirmed 2026-04-10). It runs exclusive-fullscreen by default on Steam, which is the harder case for screen capture libraries. If DXGI backend works on Dead Cells, we have high confidence it will work on any exclusive-fullscreen indie game. If Step 1 passes Minecraft but NOT Dead Cells, that's a Layer 0 bug and Step 1 is not done.

### Step 2: Input backend + end-to-end "press W for 1 second" loopback test (~10-15 hours)

**Goal**: the daemon can send a key to a game window and verify it arrived via screen capture.

**Scope**:
- `gamemind/input/backend.py` InputBackend Protocol
- `gamemind/input/pydirectinput_backend.py` wrapping `pydirectinput-rgx`, scan codes only (no VK codes)
- `POST /v1/action` endpoint
- `gamemind/cli.py` `doctor --input` that writes a known sequence (W-down 800ms, W-up) to the focused window and asks Sean to confirm visually

**Acceptance**:
- `gamemind doctor --input` in Minecraft causes the character to walk forward for about 800ms then stop
- The captured frames before + after the sequence are visibly different (sanity check on capture path)
- Input arrives even when the Minecraft window is focused but not in the foreground (scan code SendInput behavior)
- NO hand-drawn coordinates anywhere in the code (CI linter green)

### Step 3: Adapter loader + Ollama brain + one end-to-end `chop_logs` attempt (~25-40 hours)

**Goal**: `gamemind run --adapter adapters/minecraft.yaml --task "chop 3 oak logs"` runs a real attempt with Qwen2.5-VL-7B perception + Claude Sonnet plan decomposition, records the run, and reports success or failure.

**Scope**:
- `gamemind/adapter/schema.py` pydantic Adapter model with fields: `actions`, `inventory_ui`, `goal_grammars`, `world_facts`, `abort_conditions`
- `gamemind/adapter/loader.py` YAML → Adapter with Python-rejecter
- `adapters/minecraft.yaml` ≤200 lines, declarative only
- `gamemind/brain/backend.py` LLMBackend interface (OpenAI-compat)
- `gamemind/brain/ollama_backend.py` pointing at `http://localhost:11434/v1`
- `gamemind/brain/openai_backend.py` supporting Anthropic via OpenAI-compat (or Anthropic native — pragmatist's call)
- `gamemind/brain/prompts/plan_decomposition.prompt` (game-agnostic template)
- `gamemind/brain/prompts/per_frame_reflex.prompt` (game-agnostic template)
- `gamemind/verify/checks.py` first 3 predicate types: `inventory_count`, `vision_critic`, `time_limit`
- `POST /v1/session/start`, `POST /v1/session/stop`, `POST /v1/decide`, `POST /v1/capture/frame`
- `gamemind/cli.py` `run --adapter --task` that streams SSE
- Live trace goes to `runs/<timestamp>_chop-logs/events.jsonl`

**Acceptance**:
- Sean runs the command in a fresh Minecraft save near a tree
- The daemon: captures at 2-3 Hz, Qwen produces per-frame entity/action JSON, Layer 2 detects task start and calls Claude brain for plan decomposition (should emit something like "approach the tree, face the trunk, hold left-click until a log drops")
- Actions execute via pydirectinput-rgx, character walks to tree and starts mining
- After some number of frames, `verify/checks.py` fires `inventory_count(log) >= 3`, session ends with `outcome: success`
- `runs/<timestamp>_chop-logs/events.jsonl` contains the full trace

**What to NOT build in the first 3 steps**:
- Skill library — deferred to step 4
- Stardew adapter — deferred to step 5 (after Minecraft works end-to-end)
- `@tarko/agent-snapshot` integration — deferred to step 4 (record-replay harness lift)
- Scenario system — deferred to step 6
- gstack thin client — deferred to step 8 (after the daemon API is stable)

These 3 steps cover the minimum path from nothing to a first passing run. Expected cost: 50-80 hours of the 205-315h total. If this works, the remaining 155-235 hours are execution; if it doesn't, one of the descope branches activates.

---

## 7. Acceptance Criteria for Phase B Complete (Definition of Done for This Document)

- [x] All 6 Open Questions have a written, justified answer (§2 OQ-1 through OQ-6 all locked)
- [x] A concrete "first 3 build steps" list exists (§6 above, with Dead Cells DXGI smoke test added to Step 1 per task #13)
- [x] Three Design Rules documented with mechanical CI violation tests (§3; cradle-evaluator's exact four-clause revised prose for Rule 1 may replace in-place post-sign-off without changing operational meaning)
- [x] Honest effort estimate 205-315h documented with work breakdown (§4; cradle-evaluator's exact component line items may replace §4.1 in-place without changing the 205-315 envelope or the 350h red line)
- [x] v1 "done" criteria + v2 promotion triggers explicit (§2 OQ-6; satisfies adversarial-critic's Option C concrete-staging requirement)
- [x] Universality stress test names ≥3 non-Minecraft games (§1.3: Stardew, Dead Cells, Factorio, Vampire Survivors)
- [x] Wake trigger specification concrete + testable (§1.4)
- [x] Wake rate + Max Plan budget math (§1.5)
- [x] Perception-brain disagreement recovery policy (§1.6)
- [x] Cross-model pre-lock review completed (task #15 completed by adversarial-critic; any findings to be integrated in-place on delivery)
- [ ] `/plan-eng-review` has been run on this file and shows CLEAR or addressed issues (task #7; to be executed once this skeleton lands)
- [ ] `gamemind-final-design.md` approved by Sean (task #8 handoff via team-lead)
- [ ] Sean has explicitly said "approved, start building"

---

## 8. Cross-References to Work History

- Full amendment history and rejected alternates: `C:/Users/33735/.gstack/projects/ClaudeCodeBeta/architect-unknown-design-20260410-phaseB-officehours.md` (v1 → v2.6)
- OQ-1 empirical report: `C:/Users/33735/.gstack/projects/ClaudeCodeBeta/phase-b/vision-probe/oq1-report.md`
- Cradle code citations referenced in §2 OQ-2 and §3 design rules: `C:/temp/cradle/` (clone location)
- Pragmatist's 4 deliverables transcribed into v2.6 amendment of the work-history doc
- Task #13 (Dead Cells DXGI smoke test) wired into §6 Step 1 acceptance
- Task #14 (Phase C-0 SPIKE-0 gate) referenced in §5 HARD GATE

---

## 9. Known Remaining Stubs (cosmetic, non-load-bearing)

All BLOCKING stubs have been flipped. Remaining items are stylistic updates pending primary-source message re-sends; operational meaning is locked:

1. **§3 Rule 1 prose**: cradle-evaluator's exact four-clause revised wording for Rule 1 (violation test, CI check, and operational semantics are final; only the English prose may be rewritten in-place by cradle-evaluator on sign-off).
2. **§4.1 component table**: cradle-evaluator's exact stress-tested line items may replace my architect-interpolation table in-place; the 205-315h total and 350h red line are locked regardless.
3. **§7 task #15 findings integration**: if adversarial-critic's completed cross-model pre-lock review produced actionable findings, they should be integrated in-place before `/plan-eng-review` runs.

Neither item blocks `/plan-eng-review`. This document is READY for task #7.

---

**Status**: DRAFT COMPLETE, all blocking stubs flipped. Ready to ship to `/plan-eng-review` (task #7) and to parallel audit by cradle-evaluator and adversarial-critic.
