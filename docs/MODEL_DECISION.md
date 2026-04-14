# Model Decision Log — Layer 1 Perception

**Purpose**: Single source of truth for which Layer 1 model is currently authoritative. This file is append-only: new decisions add entries at the top, older entries are retained as historical record but NEVER deleted.

**CI enforcement**: `.github/workflows/model-decision-check.yml` grep-matches the "Current" entry below against: `GAMEMIND_OLLAMA_MODEL` default in `gamemind/daemon/lifespan.py` + `DEFAULT_MODEL` in `gamemind/perception/ollama_backend.py` + `ollama pull` commands in `docs/install.md` + `DEFAULT_MODEL` in `phase-c-0/probe/client.py`. Any PR that mutates one without all must update this log FIRST.

---

## Current: gemma4:26b-a4b-it-q4_K_M

**Decided**: 2026-04-13 via commit 7cc5c40 (PR #36)
**Empirical basis**: `phase-c-0/results/report-20260413-130949.json`
**Gate results**:
- T1 block accuracy: 100% (n=6, min 50%)  PASS
- T3 UI state: 75% (n=4, min 70%)  PASS
- T4 spatial: 91.7% (n=6, min 70%)  PASS
- p90 blocking latency: 721ms (max 1500ms)  PASS
- JSON reliability: 100% (min 95%)  PASS
- T2 hotbar: 33% (non-blocking reference)

**Why chosen over prior**: 17% higher T1, 1.9x faster p90 latency, same spatial & UI competence. MoE architecture (26B params, 4B active) gives quality bump without runtime cost.

**Known risks**:
- VRAM co-load with GD-tiny projected 29-30GB (may breach 28GB gate — benchmark pending)
- T3 UI dropped from 100% (qwen3) to 75% — still above 70% gate but monitor in production

---

## Superseded: qwen3-vl:8b-instruct-q4_K_M

**Decided**: 2026-04-11 via Phase C-0 probe (original `phase-c-0/C0_CLOSEOUT.md`)
**Empirical basis**: `phase-c-0/results/` (various files predating 2026-04-13)
**Superseded by**: gemma4 swap commit 7cc5c40
**Reason for replacement**: gemma4 empirical re-probe showed superior T1 (100% vs 83%) and latency (721ms vs 1353ms) while maintaining parity on other gates.

---

## Superseded: qwen2.5-vl:7b

**Decided**: 2026-04-10 in `docs/sean-approval-package.md` (pre-probe planning assumption)
**Empirical basis**: none — was the pre-C-0 candidate, never formally gated
**Superseded by**: qwen3-vl-8b-instruct (C-0 bake-off)
**Reason**: C-0 bake-off showed qwen3-8b strictly dominated on T1 / T2 / T3 / T4. See `phase-c-0/C0_CLOSEOUT.md` bake-off table.
