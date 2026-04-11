# GameMind Documentation — Reading Order

This directory contains the reference documentation for GameMind. Files are split by concern; read them in this order for a cold start:

## 1. Start here: [`install.md`](install.md)

Prerequisites (Windows 10+ / ≥8GB VRAM NVIDIA / Python 3.11 / Ollama / Anthropic API key) + 5-minute happy path + first-time cold install + troubleshooting for the top 6 failure modes.

**Time to read**: 5 minutes.
**What you'll be able to do after**: run `gamemind --version` and `gamemind adapter validate adapters/minecraft.yaml`.

## 2. Get the big picture: [`final-design.md`](final-design.md) §0 + §1

Executive Summary (§0) + Architecture (§1). This is the load-bearing design reading — 15-20 minutes to understand the two-tier hybrid (ARCH-C / Alt B2) and the 7-layer stack.

Don't read all 2400 lines yet — just §0 and §1.

**Time to read**: 15 minutes.
**What you'll understand after**: why Layer 1 is local (Qwen3-VL) and Layer 3 is sparse (Claude), why the declarative YAML adapter is the primary wedge, and what wake triggers W1-W5 mean.

## 3. Write an adapter: [`adapter-schema.md`](adapter-schema.md)

Line-by-line annotated walkthrough of `adapters/minecraft.yaml`. Covers `schema_version`, `actions`, `goal_grammars` with `SuccessCheck` / `Predicate` / `AbortCondition` composition, the deterministic action ordering rule for prompt caching, and the Amendment A9 path-traversal validation flow.

**Time to read**: 10 minutes.
**What you'll be able to do after**: write your own `adapters/<game>.yaml` that loads cleanly.

## 4. Build a backend: [`protocols.md`](protocols.md)

Frozen Protocol signatures (Amendment A12) for `LLMBackend` / `CaptureBackend` / `InputBackend` with dataclass shapes, implementation rules (never raise from network errors — always return structured error in `backend_meta`), the `backend_meta` escape hatch conventions, and example usage.

**Time to read**: 10 minutes.
**What you'll be able to do after**: implement a new `LLMBackend` (e.g. Gemini 2.5 Pro for W4 escalation) that slots into the existing daemon without touching any caller code.

## 5. Consume events: [`events-schema.md`](events-schema.md)

Amendment A2 event envelope + the closed set of 36 enumerated `event_type` values grouped by producer. Dual-file routing rules (events.jsonl + brain_calls.jsonl). Consumer rules (`ts_monotonic_ns` for ordering, `schema_version` dispatch).

**Time to read**: 10 minutes.
**What you'll be able to do after**: write an analyzer over `runs/<session>/events.jsonl` that computes session cost, prompt cache hit rate, or brain call count.

## 6. Debug an error: [`errors.md`](errors.md)

Numbered reference for all 23 exception classes (E101-E123) with cause, fix, and recoverability per error. Use this when the daemon prints `docs/errors.md#e106` in a traceback.

**Time to read**: skim the quick table (2 minutes), read individual entries as needed.

---

## Deeper reading (not required for day 1)

- [`final-design.md`](final-design.md) §2 — **The Six Open Questions**. Phase B's decision process for each major architectural choice. Good context for "why this over Cradle/UI-TARS-desktop/Computer Use API."
- [`final-design.md`](final-design.md) §3 — **Three Design Rules**. The CI-enforced hard rules, with Design Rule 4 (Amendment A3 observation tags) added 2026-04-11.
- [`final-design.md`](final-design.md) §4 — **Honest effort estimate**. 223-339 hours (revised from 205-315 after autoplan review). 350h red line.
- [`final-design.md`](final-design.md) §5 — **Phase C-0 hard gate + D1-D5 descope branches**. What to do if perception accuracy regresses.
- [`final-design.md`](final-design.md) §6 — **First 3 build steps**. The concrete Phase C Step 1 / Step 2 / Step 3 scope lists that map to `gamemind/daemon/`, `gamemind/input/`, and `gamemind/run` respectively.
- [`final-design.md`](final-design.md) §10 — **Autoplan review**. The Phase 1 CEO + Phase 3 Eng + Phase 3.5 DX review outputs, 15 applied amendments, and the 7 strategic findings still open for Sean's review.

- [`sean-approval-package.md`](sean-approval-package.md) — locked-in Phase B decisions checklist.

## Project-level instructions

For Claude Code agents working in this repo, see [`../CLAUDE.md`](../CLAUDE.md) at the project root. It covers the Phase C-specific discipline (always invoke `/autoplan` on build kickoff, never write code without reading `docs/final-design.md`, git workflow via feature branches + PRs).
