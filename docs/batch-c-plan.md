# Batch C Execution Plan

**Status**: v2 — reviewer gate YELLOW → fixes applied, proceeding
**Date**: 2026-04-11
**Context**: Batch A (real Windows capture + input) and Batch B (Amendment A1 live-perception spike) are merged to main. Batch C wires the existing building blocks into a working `gamemind run --adapter minecraft.yaml --task "chop 3 logs"` E2E path per `docs/final-design.md` §6 Step 3.

## Reviewer gate outcome (YELLOW → YELLOW-fixed)

Independent subagent reviewer (2026-04-11) flagged 6 issues against the v1 draft. Responses below; plan body updated in place.

| # | Severity | Issue | Resolution |
|---|---|---|---|
| 1 | **RED** | FreshnessQueue wired at wrong boundary in v1 loop pseudocode — existing `FreshnessQueue[PerceptionResult]` sits at perception→layer2 but Amendment A1 spec says capture→perception | Loop redesigned per Batch B spike architecture: new private `FrameSlot` class in `runner.py` holds `CaptureResult` bytes at the capture→agent boundary; perception runs synchronously in agent thread; existing `FreshnessQueue` left untouched as v0 code with a cleanup TODO. See §Architecture section below. |
| 2 | **RED** | `verify/checks.py` required by §6 Step 3 but scoped as minimal with no test | C2 now includes `gamemind/verify/checks.py` with ONLY the `inventory_count` predicate type (minecraft.yaml's only success_check) + `time_limit` + `health_threshold`, each with a dedicated unit test. `vision_critic` deferred to post-Batch-C per §6 Step 3 scope. |
| 3 | YELLOW | `WakeTriggerEvaluator.__init__` missing budget awareness | Budget is owned by the runner, NOT the evaluator. Evaluator is pure function of (perception, flags) → `WakeEvaluation`. Runner checks `budget_tracker.exceeded()` after every `brain.chat()`. Interface unchanged, responsibility documented. |
| 4 | **RED** | `ActionRepetitionGuard.record(predicate_fired)` wrong — window check needs accumulated state, not per-call bool | Interface split: `guard.mark_predicate_fired(ts_ns)` for state accumulation (called by agent loop when verify fires), `guard.record_action(action_hash, ts_ns) -> bool` for decision (returns True iff guard should fire W2 bypass). |
| 5 | YELLOW | `session/manager.py::_event_type_for_outcome` collapses `brain_rate_limited` and `brain_unavailable` to the same event_type, losing Amendment A2 audit granularity | Fix folded into C5 PR when I touch AnthropicBackend anyway. New event_type `session_aborted_brain_rate_limited` added to `events/envelope.py`. |
| 6 | YELLOW | C4 test uses relative `Path("adapters/minecraft.yaml")` — breaks under Amendment A9 path hardening + pytest cwd | Fixed: `Path(__file__).parents[2] / "adapters" / "minecraft.yaml"` via pytest fixture. |
| 7 | NTH | `MockBrainBackend` must wrap scripted responses in real `LLMResponse` | Mock contract updated — scripted responses are `LLMResponse` objects, not dicts. |
| 8 | NTH | C5 cost bound `< 0.01` too loose for a 20-token Sonnet 4.6 call | Tightened to `< 0.0005`. |

**Biggest risk reviewer called out**: synchronous perception in the main loop at 2Hz would violate freshness budget continuously. Response: **the spike architecture (Batch B, PR #17) already proved this works** — p90 inference ~420ms < 500ms tick interval, 0 drops over 120 ticks. But the spike had capture in a separate thread, NOT in the main loop. The v1 pseudocode conflated these. Fixed in §Architecture below: capture runs on its own thread exactly like the spike, agent thread consumes `FrameSlot` latest-wins + runs perception + layer2 + brain + input sequentially. This is Batch B's architecture transplanted into the production runner.

## Scope audit — what already exists

`gamemind/` currently ships (from iter-1 through iter-11 + Batch A/B):

- **Layer 0 capture**: `capture/{wgc_backend,dxgi_backend,selector,_win32}.py` — real Windows bindings, Batch A
- **Layer 1 perception**: `perception/{ollama_backend,freshness}.py` — real Ollama + Amendment A1 `FreshnessQueue`
- **Layer 3 brain**: `brain/{backend,anthropic_backend,prompt_assembler,prompts/*}` — `LLMBackend` Protocol + `AnthropicBackend` with cost estimation, streaming, adaptive thinking, prompt caching
- **Layer 4 input**: `input/{backend,pydirectinput_backend}.py` — real SendInput via pydirectinput-rgx, Batch A
- **Adapter**: `adapter/{schema,loader}.py` + `adapters/minecraft.yaml` — pydantic schema, YAML loader with path-traversal hardening
- **Session state**: `session/{manager,outcomes}.py` — thread-safe state machine (`idle → running → terminal`)
- **Events**: `events/{envelope,writer,scrub}.py` — bounded-queue writer + `scrub_secrets` + enumerated event types
- **Daemon**: `daemon/{main,lifespan}.py` — FastAPI app with `/healthz`, session start/stop, Bearer auth, A3 origin rejection

## The real gap

1. **`gamemind/layer2/` does not exist** — no stuck detector, no brain wake triggers, no action repetition guard. `events/envelope.py` declares the `layer2` producer + `stuck_detected` event type, but no code emits them.
2. **`gamemind/runner.py` does not exist** — no loop that ties Capture → Perception(FreshnessQueue) → Layer 2 → Brain(on wake) → Input → Session.
3. **`gamemind/cli.py::_cmd_run` is a TODO stub** that prints `"TODO: session manager / perception daemon / brain wakes in Step 3"` and returns 0 without doing anything.
4. **No budget guard on `AnthropicBackend`** — `_estimate_cost_usd` exists but no cumulative tracker + no `BudgetExceededError`. Amendment A10 commitment violated.
5. **`AnthropicBackend` cost constants are hardcoded Opus 4.6** (`$5/$25 per 1M`) but `model` is `__init__`-configurable. Switching to Sonnet 4.6 would silently over-estimate 1.67x. Fail-safe but wrong.
6. **No `MockBrainBackend`** — test fixture missing, can't write integration tests that don't burn API credits.

## C1: `gamemind/layer2/` module

**Files**:
- `gamemind/layer2/__init__.py` — re-exports
- `gamemind/layer2/stuck_detector.py` — §1.4 W2 trigger + §1.7 motion-quiet metric
- `gamemind/layer2/action_guard.py` — §1.8 Amendment A13 action repetition guard
- `gamemind/layer2/wake_trigger.py` — §1.4 W1-W5 dispatcher (returns `WakeReason | None` per tick)
- `tests/test_layer2_stuck_detector.py` — 3 fixtures per §1.4 W2 spec
- `tests/test_layer2_action_guard.py` — "walk into wall" synthetic
- `tests/test_layer2_wake_trigger.py` — W1-W5 rule dispatch cases

**Interfaces (frozen contracts, v2 post-reviewer)**:

```python
# stuck_detector.py
@dataclass
class StuckCheckResult:
    is_stuck: bool
    reason: Literal["motion_quiet", "no_predicate", "no_action", "multi"] | None
    metric_value: float  # 0-1 motion-quiet score

class StuckDetector:
    def __init__(self, *, stuck_seconds: float = 20.0, entropy_floor: float = 0.02): ...
    def update(self, frame_bytes: bytes, predicate_fired: bool, action_executed: bool, ts_ns: int) -> StuckCheckResult: ...
    def reset(self) -> None: ...

# action_guard.py — post-reviewer fix: split predicate state from action record
class ActionRepetitionGuard:
    def __init__(self, *, ring_size: int = 20, window_s: float = 10.0, max_repeats: int = 5): ...
    def mark_predicate_fired(self, ts_ns: int) -> None:
        """Agent loop calls this when verify fires a predicate. Guard
        accumulates a rolling list of predicate-fired timestamps so
        `record_action` can check the window without per-call state."""
    def record_action(self, action_hash: str, ts_ns: int) -> bool:
        """Record an action. Returns True iff guard should force W2
        stuck trigger per Amendment A13: same action_hash >5 times in a
        10s window AND no predicate_fired in the same window."""
    def reset(self) -> None: ...

# wake_trigger.py — evaluator is PURE function of inputs, no budget awareness
WakeReason = Literal["w1_task_start", "w2_stuck", "w3_abort", "w4_critic", "w5_verify"]

@dataclass
class WakeEvaluation:
    reason: WakeReason | None
    payload: dict[str, Any]

class WakeTriggerEvaluator:
    def __init__(self, *, stuck: StuckDetector, guard: ActionRepetitionGuard, adapter: Adapter): ...
    def on_session_start(self) -> WakeEvaluation: ...  # always W1
    def on_perception_tick(
        self,
        result: PerceptionResult,
        predicate_fired: bool,
        action_executed: bool,
        last_action_hash: str | None,
    ) -> WakeEvaluation: ...
    def on_success_candidate(self, result: PerceptionResult) -> WakeEvaluation: ...  # W5
```

**Risks**:
- Motion-quiet metric depends on frame decoding (WEBP → numpy). Need numpy. Already a dependency.
- `predicate_fired` signal comes from the verify engine which is only partially built. For C1 I'll accept a bool parameter and let C2's runner wire it up.

**Reviewer lens**: Does the interface obey Amendment A12 Protocol discipline (no hidden state, deterministic on inputs, raises `gamemind.errors.*` only, never silently swallow)?

## C2: `gamemind/runner.py` + `MockBrainBackend` + minimal `verify/checks.py`

**Files**:
- `gamemind/runner.py` — agent runner (includes private `FrameSlot` class)
- `gamemind/verify/__init__.py` + `gamemind/verify/checks.py` — minimal predicate evaluator
- `gamemind/brain/mock_backend.py` — scripted `MockBrainBackend` satisfying `LLMBackend` Protocol
- `gamemind/brain/budget_tracker.py` — cumulative cost tracker + `BudgetExceededError`
- `tests/test_runner_smoke.py` — instantiation + one-tick sanity with mocks
- `tests/test_runner_dry_run.py` — full E2E: mock capture + mock perception + mock brain, asserts `outcome: success` + W1+W5 call_count
- `tests/test_verify_checks.py` — inventory_count / time_limit / health_threshold predicate evaluation
- `tests/test_budget_tracker.py` — cumulative tracking + hard-cap enforcement

**Runner contract** (v2 post-reviewer):

```python
@dataclass
class RunnerConfig:
    adapter: Adapter              # pre-loaded, NOT path (avoids Amendment A9 path issues in tests)
    task: str
    runs_root: Path
    capture: CaptureBackend
    perception: LLMBackend        # Layer 1 (Ollama in prod, Mock in dry-run)
    brain: LLMBackend             # Layer 3 (Anthropic in prod, Mock in dry-run)
    input: InputBackend
    hwnd: int
    budget_usd: float = 0.30      # session hard cap
    tick_hz: float | None = None  # defaults to adapter.perception.tick_hz

class AgentRunner:
    def __init__(self, config: RunnerConfig, session_manager: SessionManager, events_writer: EventWriter): ...
    def run(self) -> Outcome: ...  # blocks until terminal
    def stop(self) -> None: ...
```

**Architecture (v2 — Batch B spike transplant)**:

```
┌──────────────────────┐                       ┌─────────────────────────┐
│  Capture Thread      │                       │  Agent Thread           │
│  ──────────────      │                       │  ─────────────          │
│  tick at 2 Hz:       │                       │  1. W1 brain call       │
│    cap = wgc.capture │ ──► FrameSlot[1] ──►  │  2. loop until terminal:│
│    slot.put(cap)     │     (latest-wins,     │     - cap = slot.take() │
│    (drops if full)   │      drops on put)    │     - if stale: skip    │
└──────────────────────┘                       │     - perception = ollama│
                                               │       .chat(cap.bytes)  │
                                               │     - if stale: skip    │
                                               │     - fired = verify(   │
                                               │         perception)     │
                                               │     - if fired: W5      │
                                               │     - wake = trigger    │
                                               │       .evaluate(...)    │
                                               │     - if wake:          │
                                               │       resp = brain.chat │
                                               │       budget.record     │
                                               │       if exceeded: ABORT│
                                               │       actions = parse   │
                                               │       input.send        │
                                               │       guard.record      │
                                               └─────────────────────────┘
```

This is Batch B spike's proven pattern: capture in its own thread pushing raw bytes into a size-1 latest-wins slot; agent thread consumes + runs Ollama synchronously + Layer 2 + Brain + Input, all sequential. Batch B's `LatestWinsSlot[CapturedFrame]` becomes `runner.py`'s private `FrameSlot[CaptureResult]`. The existing `gamemind/perception/freshness.py::FreshnessQueue` is NOT touched — it was early v0 code; clean up TBD in a follow-up PR.

**Verify engine scope** (minimal for Batch C):
- `inventory_count` predicate type (only one minecraft.yaml success_check uses)
- `time_limit` predicate type (minecraft.yaml abort_conditions)
- `health_threshold` predicate type (minecraft.yaml abort_conditions)
- `vision_critic` — **NOT in scope**, deferred. Runner raises `NotImplementedError` if adapter requires it.
- Implementation: pure function `check_predicate(pred: Predicate, perception_result: PerceptionResult, session_start_ns: int) -> bool`. No state, no I/O.

**Budget tracker contract**:

```python
class BudgetTracker:
    def __init__(self, limit_usd: float): ...
    def record(self, cost_usd: float) -> None: ...  # raises BudgetExceededError if over
    def total_usd(self) -> float: ...
    def exceeded(self) -> bool: ...
```

Integrated into runner's brain-call path. Exceeded → runner aborts with `outcome: runaway` (cleaner than a new outcome; the 30-call upper bound and budget cap are both "runaway").

**Risks (updated)**:
- **Perception latency vs tick rate** (reviewer flagged): resolved by spike-architecture clone — capture thread decoupled from agent thread, agent thread runs perception inline, at 2Hz with p90 ~420ms Ollama latency the spike showed 0 drops. No change needed from Batch B's proven design.
- **Prompt assembly for W1/W2/W5**: `brain/prompt_assembler.py` already exists. Need to verify W1 (plan decomposition), W2 (replan), W5 (verify) templates are present in `brain/prompts/templates/`. If missing, C2 adds inline stub + flags for follow-up PR.
- **Amendment A6 stale-reuse window**: needs to be implemented in the agent loop's stale check — not "discard and move on" but "keep previous perception and increment staleness counter up to 3 ticks". Cleaner than inline: separate `StaleReuseTracker` helper in runner.
- **Thread shutdown ordering**: capture thread must join cleanly on `runner.stop()`, otherwise daemon shutdown hangs. Use `threading.Event` pattern from spike.

**Reviewer lens** (for C2 PR review): Does the loop strictly obey §1.1.A latest-wins semantics with capture→agent boundary? Does it emit every required `layer2` / `brain` / `action` / `session` event_type? Does it honor Amendment A6 stale-reuse window? Does the budget tracker fire BEFORE the brain call returns or after (must be after — we pay for the call regardless once it's issued)?

## C3: `gamemind/cli.py::_cmd_run` wire-up + `--dry-run` flag

**Changes**:
- Add `--dry-run` flag to the `run` subparser (default False). Forces MockBrainBackend + MockCapture + MockInput.
- Fill in `_cmd_run`: load adapter, resolve backends (real or mock), find target HWND, instantiate `AgentRunner`, call `run()`, print outcome.
- Add `--window-title` to `run` (optional, same semantics as `doctor --capture`).
- Print cumulative cost estimate at session end.

**Risks**: None material. Mostly glue.

## C4: MockBrainBackend fixture + full dry-run E2E integration test

**Already scaffolded in C2** — this task is about writing a REAL test, not just a smoke test:

```python
def test_runner_dry_run_chop_logs_succeeds(tmp_path):
    """Full E2E with mock capture + mock brain, asserts session reaches success."""
    # Absolute path to avoid pytest cwd issues + Amendment A9 path hardening
    adapter_path = Path(__file__).parents[1] / "adapters" / "minecraft.yaml"
    adapter = load_adapter(adapter_path)
    mock_brain = MockBrainBackend(scripted=[
        # W1 plan decomposition — returned as LLMResponse, not dict
        LLMResponse(text='{"plan": ["approach_tree", "face_trunk", "attack"]}',
                    parsed_json={"plan": ["approach_tree", "face_trunk", "attack"]},
                    prompt_tokens=1200, completion_tokens=80,
                    cost_estimate_usd=0.005, latency_ms=500.0,
                    request_id="test-w1", cached_system=False),
        # W5 verify
        LLMResponse(text='{"verify_ok": true}',
                    parsed_json={"verify_ok": True},
                    prompt_tokens=1400, completion_tokens=20,
                    cost_estimate_usd=0.005, latency_ms=400.0,
                    request_id="test-w5", cached_system=True),
    ])
    mock_capture = ScriptedCapture(frames=[
        load_fixture("tree_visible.webp"),  # tick 0-10: approaching
        load_fixture("tree_close.webp"),    # tick 11-15: facing trunk
        load_fixture("log_in_hand.webp"),   # tick 16-20: inventory filled
    ])
    mock_perception = ScriptedPerception(responses=[
        {"entities": ["oak_log"], "inventory": {"log": 0}},
        {"entities": ["oak_log"], "inventory": {"log": 0}},
        {"entities": [], "inventory": {"log": 3}},
    ])
    runner = AgentRunner(...)
    outcome = runner.run()
    assert outcome == "success"
    assert mock_brain.call_count == 2  # W1 + W5
```

**Risks**: fixture images might not exist. Use deterministic mock capture that returns raw bytes regardless.

## C5: `scripts/test_anthropic_ping.py` — live smoke + pricing fix + event_type split

**What**: A standalone script (not pytest, per our convention for scripts that need real network) that:
1. Loads `.env.local`
2. Instantiates `AnthropicBackend` with `model="claude-sonnet-4-6"` + a tight `max_tokens=20`
3. Calls `.chat()` with a `"Reply with exactly: pong"` prompt
4. Asserts `response.text == "pong"`, `response.cost_estimate_usd > 0`, `response.cost_estimate_usd < 0.0005`
5. Prints detailed cost breakdown + validates budget tracker integration

**Also in this PR**:
- Fix the Opus-hardcoded pricing bug in `anthropic_backend.py`. Refactor `_estimate_cost_usd` to accept a `model_id` and dispatch to the right price table. Add Sonnet 4.6 ($3/$15 per 1M) + Haiku 4.5 ($1/$5 per 1M) + Opus 4.6 ($5/$25 per 1M).
- Fix the `session/manager.py::_event_type_for_outcome` pre-existing bug: `brain_rate_limited` maps to new `session_aborted_brain_rate_limited` event_type (added to `events/envelope.py` enumeration).

**Cost**: ~$0.0002 per call × 2-3 calls = ~$0.001. Noise.

## Execution discipline

- Each of C1-C5 gets its own feature branch + PR
- Each PR spawned reviewer subagent BEFORE `gh pr create`, using `final-design.md` + diff + lens prompt
- Each PR commit message includes: "gstack skill bypassed: /plan-eng-review; reason: autonomous session, replaced with subagent reviewer gate"
- Any reviewer red → fix + re-review
- Any design ambiguity I can't resolve from the spec → stop + checkpoint to Sean
- Session API cost hard cap $0.30 (reviewer subagents ~$0.10, C5 ping ~$0.01, buffer $0.19)
- All work on `main` via squash merge, no rebase onto old branches

## Stop criteria (checkpoint + wait for Sean)

- Reviewer flags architectural issue I can't fix without final-design.md amendment
- Test failure root cause isn't obvious within one debug cycle
- Amendment A1 / A6 / A10 / A12 / A13 contract would need weakening to proceed
- Cumulative spend approaches $0.25 (75% of budget)
- Any destructive or shared-state action beyond "push feature branch + open PR"

## What Sean sees when he returns

`runs/batch-c-checkpoint.md` with:
- List of merged PRs + commit SHAs
- Reviewer report per PR (pass/fail/comments)
- Cumulative API spend
- Any open questions that stopped progress
- Exact command to run for C6 live chop_logs (needs Minecraft + chosen tree)
