# GameMind — Sean's Approval Package

**Phase B output** — consolidated for Sean's approval decision
**Generated**: 2026-04-10
**Full design doc**: `gamemind-final-design.md` (480 lines, architect-written, team-audited)
**This document**: short summary for the approve/reject decision only

---

## What you're approving

A universal game AI agent framework that plays video games via vision + OS-level input. Architecture is **two-tier hybrid**: Qwen2.5-VL-7B runs locally on your RTX 5090 as continuous perception (1-2 Hz), Claude 4.5 Sonnet runs via your Max Plan API as sparse brain on wake events (5-20 calls per task).

**The elevator pitch** (architect's words, team-approved):
> "GameMind is one binary that's declarative over two axes — games (via YAML adapter) and models (via OpenAI-compat LLMBackend). Same daemon, swap data not code, on both dimensions."

**v1 demo**: Complete one task in Minecraft + one task in Stardew Valley using ONLY a different adapter YAML between the two games. This two-game cross-transfer proves the universality claim in the MVP.

**Differentiation vs Cradle, UI-TARS-desktop, Lumine, JARVIS-1**: first runtime to ship a **declarative game adapter layer** on top of a general-purpose GUI agent stack. Neither Cradle (hand-drawn click maps) nor UI-TARS-desktop (per-platform operators, not per-game knowledge) achieves this. That's the wedge.

---

## The critical numbers (no hiding)

**Honest commitment: 205-315 hours / 5-10 weeks calendar time**

At 20-30 hours/week solo-developer pace with Claude Code + gstack assistance.

This is the cradle-evaluator stress-tested number. Earlier estimates (90-140h in v2.1, 60-80h in v2.5) were wrong — they omitted debug time, dev-loop integration overhead, and solo-dev multipliers. Adversarial-critic's stress test forced the honest revision.

**Budget math (fits your Max Plan)**:
- Claude brain cost: ~$22/month (60-120 wakes/hour × $0.03/wake × 8 hours/month)
- Qwen local cost: ~$0.56/month (power only)
- **Total variable cost**: ~$22.56/month
- **Max Plan budget**: $100/month
- **Remaining**: $77.44/month for other Claude Code work

**Calendar planning**:
- Optimistic (200 hours ÷ 40 h/wk): 5 weeks
- Realistic (260 hours ÷ 30 h/wk): ~9 weeks
- Pessimistic (315 hours ÷ 20 h/wk): ~16 weeks

**Plan for 8-12 weeks. Prepare for up to 16 if real life intervenes.**

---

## Your 4 approval decisions

### ☐ Decision 1: Accept the 205-315 hour / 5-10 week commitment

This is a real commitment. Say yes only if you can actually carve out the time. If you can't do 20-30 hours/week consistently, calendar weeks balloon and motivation erodes.

**My honest check-in**: Is this where you want to spend your next 2-3 months? The gstack learning goal could be achieved with a smaller project in 2-3 weeks. GameMind is a real project that happens to also teach gstack. If your primary goal is still "learn gstack," a smaller project might serve you better. If your primary goal is "build an impressive thing that demonstrates the architectural insight Sean had," GameMind is the right target.

Say yes only if you're committing to the latter.

### ☐ Decision 2: Confirm Option C (staged) framing

Already your answer (C). Locked in. The final design doc's §2 OQ-6 section has the concrete v2 trigger events you need to make Option C non-vague:

- 2+ games working via pure YAML adapter delta with zero code change (v1 baseline)
- Phase C-0 passes ≥80% on all 4 task categories
- Third-game universality proof (RTS, racing, twin-stick, rhythm — structurally different)
- Community fork or external PR contribution
- Skill library demonstrates cross-session compounding

If any ONE of these fires within 3 months post-v1, promote to v2 (research artifact). If none fire, v1 stays as personal tool. That's an acceptable outcome.

### ☐ Decision 3: Commit to running Phase C-0 probe BEFORE build begins

**This is the single most important gate in the project.** Phase C-0 is a ~2 hour activity you do on your RTX 5090 BEFORE writing any GameMind code. The design doc has detailed steps in §6.

TL;DR of what you do:
1. Install Ollama, pull qwen2.5vl:7b (~5GB, ~30 min)
2. Capture 20+ hard-case Minecraft screenshots (cluttered inventory, caves, night, combat, particles) (~30 min)
3. Label them manually for ground truth on 4 task types (block ID, inventory, UI state, spatial) (~45 min)
4. Run a probe script through Ollama, score against ground truth (~30 min)
5. Report: pass if ≥80% on ALL 4 categories, fail otherwise

**Why it matters**: there is currently ZERO public data on Qwen2.5-VL-7B accuracy on Minecraft game vision. The team's recommendation is based on extrapolation from DocVQA/Android Control benchmarks. The recommendation might be wrong. Phase C-0 is how we find out BEFORE writing thousands of lines of dependent code.

**If it passes**: proceed to Phase C build.
**If it fails**: pick a fallback branch from decision 4.

### ☐ Decision 4: Accept the pre-enumerated fallback chain (D1-D5) as descope decision space

If Phase C-0 fails, we don't improvise. We pick from these branches:

- **D1**: Prompt/adapter hints retry (~1 day) — for close-miss 70-79% cases
- **D2**: Upgrade to Qwen2.5-VL-32B Q4 in 32GB VRAM (~1 day) — larger model, slower
- **D3**: Add Gemini 2.5 Pro critic layer for hard cases (~3 days) — hybrid with cloud critic
- **D4**: Descope v1 target to 2D pixel-art game (Stardew primary, Factorio/Vampire Survivors secondary) — refocus week
- **D5**: Last resort — v1 becomes scaled-down PoC with `--dev-checkpoint` manual mode

You pick the branch when/if C-0 fails. The team has pre-enumerated the options so we don't stall.

---

## The 3 Design Rules (architect will enforce via CI)

These are the **hard rules** that make GameMind different from Cradle. Architect has committed to them as binding constraints, and cradle-evaluator will audit if Phase C drifts.

### Rule 1: No hand-authored coordinates
No `mouse_move(x, y)` with literal integers in the action layer. All targets come from runtime vision grounding (Qwen/UI-TARS). CI grep enforces.

### Rule 2: No per-game Python in Layer 6
Adapters are pure YAML. No `if game == "minecraft"` branches. Build check enforces.

### Rule 3: Per-game prompts stay generic
Prompt templates query adapter fields by name. Game knowledge lives in YAML data, not prompt prose. Violation test: "if removing the game name from the prompt would break task execution, the prompt is violating this rule."

---

## What the team built during Phase B (you should feel good about this)

**6 teammates in parallel, independent context windows, ~2 hours of wall-clock work** — produced:

1. **One 480-line final design doc** (architect) with 6 OQ answers, evolution log, universality stress test, fallback branches
2. **One empirical OQ-1 report** (vision-researcher) with cost tables, latency analysis, Qwen primary recommendation, Orak gotcha correction, Game-TARS closure
3. **One 5-phase Cradle evaluation** (cradle-evaluator) that found the hand-drawn click map smoking gun, delivered LEARN-FROM verdict, stress-tested the honest hours to 205-315
4. **One UI-TARS-desktop feature matrix** (cradle-evaluator) that killed pragmatist's narrow differentiation defense with file:line evidence — saving us from shipping a built-on-air claim
5. **One implementation stack spec** (pragmatist) with Ollama + FastAPI + pydirectinput + windows-capture + CaptureBackend adapter + pre-computed B1/B2 IPC schemas
6. **One 30-item challenge list + in-process pre-lock audit** (adversarial-critic) with 3 HIGH severity findings and B2 4-kill-shot attack matrix that forced architect to specify wake triggers concretely

**The team caught 3 Phase A errors you would have otherwise built on top of**:
1. Orak Claude Minecraft 75.0 score was text-state, not vision (Phase A research pack cited it as vision evidence)
2. Game-TARS 72% embodied number applied to a specific benchmark that never tested Claude/GPT-5/Gemini on Minecraft (Phase A cited it as a cross-model comparison)
3. Pragmatist's initial "runtime as differentiation" framing was wrong — UI-TARS-desktop already ships 3 of 5 features claimed as missing

**Phase A by itself would have committed to wrong numbers, wrong vision model assumptions, and a dead differentiation argument.** Phase B agent teams caught all three before they hit your final design. This is the value of the structured adversarial process.

---

## Risks you're committing to (read carefully)

1. **Qwen2.5-VL-7B accuracy on Minecraft is UNVERIFIED**. This is the #1 risk. Phase C-0 exists specifically to measure it. If it fails and all fallback branches are unpalatable, the project stalls.

2. **The 205-315 hour estimate is honest but not certain**. Real project hours can still balloon if unexpected integration problems hit. Plan for up to 16 calendar weeks, not 5.

3. **@tarko/agent-snapshot is TypeScript — Python integration requires bridge**. SPIKE-1 in Phase C determines whether we use Node.js sidecar subprocess or HTTP. If neither works cleanly, we fall back to rebuilding record-replay ourselves (+15-20 hours).

4. **vLLM on Windows requires WSL2**. Ollama is the primary path specifically to avoid this. If Ollama throughput is insufficient (unlikely at 1-2 Hz target), vLLM escape hatch adds Windows+WSL2 complexity.

5. **Solo developer on a 10-week project is a motivation test**. Week 1-3 is exciting, week 4-6 is grind, week 7+ is where most personal projects die. Plan for the grind phase mentally.

6. **gstack as a learning goal vs building as the primary goal — do not confuse them**. If GameMind starts to feel like a chore, remember: you started this to learn gstack's six-layer agent orchestration. You've already achieved a huge portion of that goal just by completing Phase B. Phase C is where the GameMind ship goal takes over. If Phase C loses steam, it's OK to shelve and still count the gstack learning as won.

---

## My honest recommendation

**Approve all 4 decisions if AND only if**:
- You can carve out 20-30 hours/week for 8-12 weeks
- You're genuinely excited about building a universal game agent framework, not just "learning gstack by building something"
- You're OK with the ~20% chance that Phase C-0 fails and you have to descope (D3 hybrid is the most likely fallback; D4 pixel-art refocus is ~30% survival rate)

**Do NOT approve if**:
- You feel obligation rather than excitement
- You're likely to have life disruptions (travel, grad school applications, job hunt) in the next 2-3 months
- You were more interested in the gstack learning itself than the GameMind project

Remember: **saying "not now" is not failure**. You already got the gstack learning outcome. GameMind v1 can wait until your calendar is ready.

---

## If you approve

1. You sign the checklist (below)
2. I call `TeamDelete` to clean up Phase B team (they're already standing down)
3. Phase B is complete. Task list updated.
4. **Next session**: you open Claude Code, load this final design doc + context, start Phase C with Step 1 (environment setup) from `gamemind-final-design.md` §6 Step 1.
5. Phase C will be a DIFFERENT team composition — implementation agents, not design agents. Start fresh.

---

## Sean's Signature

- [ ] **Decision 1**: I accept the 205-315 hour / 5-10 week (realistic 8-12 week) commitment: ______
- [ ] **Decision 2**: I confirm Option C (staged, v1 personal tool → v2 research artifact) with the concrete triggers in §2 OQ-6: ______
- [ ] **Decision 3**: I commit to running Phase C-0 probe on my RTX 5090 BEFORE any Phase C build work begins: ______
- [ ] **Decision 4**: I accept the D1-D5 fallback chain as pre-enumerated descope decision space: ______

**Signature**: ________________
**Date**: ________________

Or alternatively:

- [ ] **NOT APPROVED**: __________________ (reason — the team will stand down, design doc stays as reference, no hard feelings)

---

**END OF APPROVAL PACKAGE**

Questions? Ask me. I'm the team-lead and I'll either answer directly or point you to the specific section of `gamemind-final-design.md` that has the detail.
