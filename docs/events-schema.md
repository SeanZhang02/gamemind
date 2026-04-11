# events.jsonl + brain_calls.jsonl Schema Reference

Every observability path in GameMind routes through `gamemind/events/`
writers. This is Amendment A2 — the single biggest guard against the
"5 producers drift their schemas in 5 directions over 3 weeks" failure
mode the eng subagent flagged as CRITICAL.

> Source of truth: `gamemind/events/envelope.py::Envelope`. This doc
> annotates the envelope and enumerates every `event_type` in the
> closed set.

## Envelope

Every line in `events.jsonl` and `brain_calls.jsonl` is one JSON object
with this exact shape:

```json
{
  "schema_version": 1,
  "session_id": "abc123-uuid",
  "ts_monotonic_ns": 1234567890123,
  "ts_wall": "2026-04-11T14:23:05.123456+00:00",
  "frame_id": "frame-042",
  "producer": "perception",
  "event_type": "perception_tick",
  "payload": { "frame_age_ms": 142.7, "latency_ms": 287.3 }
}
```

### Field contract

| Field | Type | Required? | Notes |
|---|---|---|---|
| `schema_version` | int | ✓ | Currently `1`. Breaking changes bump this and require a migration. |
| `session_id` | str | ✓ | UUID4-ish per session. Allows cross-file joins. |
| `ts_monotonic_ns` | int | ✓ | `time.monotonic_ns()` at envelope construction. **Use this for ordering** — wall clock is unreliable across NTP corrections. |
| `ts_wall` | str | ✓ | ISO 8601 UTC with timezone suffix. For human reading / cross-machine correlation. |
| `frame_id` | str \| null | ✓ (nullable) | Frame ID the event was emitted against, or null for producer-level events. |
| `producer` | Literal | ✓ | One of the 8 producers below. |
| `event_type` | str | ✓ | One of the enumerated types below (validated by loose runtime check). |
| `payload` | dict | ✓ | Free-form per event_type. `scrub_secrets()` is applied before write. |

## Producers (closed set)

| Producer | Owns |
|---|---|
| `capture` | Layer 0 capture events |
| `perception` | Layer 1 perception events |
| `layer2` | Layer 2 trigger detector events |
| `brain` | Layer 3 brain wake + response events |
| `verify` | Predicate / disagreement / arbiter events |
| `action` | Layer 4 input execution events |
| `replay` | Step 4+ replay harness events |
| `session` | Session lifecycle events |

## Event types (closed set, 36 total)

Additions require a PR to `gamemind/events/envelope.py::_KNOWN_EVENT_TYPES` + this doc.

### capture (3)

- `capture_ok` — frame captured successfully. Payload: `{backend: WGC|DXGI, variance: float, width: int, height: int}`
- `capture_black_frame` — frame variance below `VARIANCE_FLOOR`. Payload: `{backend: ..., variance: float, consecutive_count: int}`
- `capture_backend_swap` — selector swapped WGC → DXGI or vice versa. Payload: `{from: WGC|DXGI, to: WGC|DXGI, reason: str}`

### perception (4)

- `perception_tick` — one inference completed. Payload: `{frame_age_ms: float, latency_ms: float, json_ok: bool}`
- `perception_stale_dropped` — frame discarded due to `frame_age_ms > freshness_budget_ms` (Amendment A1). Payload: `{frame_age_ms: float, budget_ms: float}`
- `perception_json_error` — `json.loads()` failed on model output. Payload: `{raw_text_preview: str (first 200 chars), backend: ollama}`
- `perception_think_leak` — `<think>` tag detected in response (Amendment A14). Payload: `{backend: ollama, model: str}`

### layer2 (2)

- `stuck_detected` — Amendment A4 stuck detector fired all three conditions. Payload: `{motion_metric: float, stuck_seconds: float, predicate_progress: bool}`
- `abort_condition_fired` — adapter abort condition triggered. Payload: `{condition_type: str, current_value: any, threshold: any}`

### brain (8)

Also written to `brain_calls.jsonl` (brain-only scan for v2-T2 skill-compounding metric).

- `wake_w1` — task start. Payload: `{trigger: "task_start", plan: str, latency_ms: float, cost_usd: float}`
- `wake_w2` — stuck detector replan. Payload: `{trigger: "stuck", stuck_seconds: float, latency_ms: float, cost_usd: float}`
- `wake_w3` — abort/stalled eval. Payload: `{trigger: "abort_condition", latency_ms: float, cost_usd: float}`
- `wake_w4` — vision critic escalation. Payload: `{trigger: "vision_critic_unclear", critic_question: str, latency_ms: float, cost_usd: float}`
- `wake_w5` — task completion verify. Payload: `{trigger: "success_check", verified: bool, latency_ms: float, cost_usd: float}`
- `brain_response_ok` — successful brain response. Payload: `{input_tokens: int, output_tokens: int, cached_system: bool, cost_usd: float}`
- `brain_response_error` — brain returned error (rate limit / 5xx / timeout). Payload: `{error_code: str, error_msg: str (scrubbed)}`
- `brain_rate_limited` — 429 encountered, backoff engaged. Payload: `{attempt: int, backoff_s: float}`

### verify (5)

- `predicate_fired` — a verify predicate evaluated true. Payload: `{predicate_type: str, predicate_value: any}`
- `perception_disagreement` — Layer 1 and Layer 3 returned contradictory answers on the same frame (§1.6). Payload: `{layer1: any, layer3: any, question: str}`
- `self_correction` — Layer 1 re-query at `temperature=0` changed its answer (§1.6 step 1). Payload: `{original: any, corrected: any}`
- `layer_1_majority_wins` — Layer 1 cross-frame sanity check beat Layer 3 (§1.6 step 2). Payload: `{layer1_votes: list, layer3: any}`
- `arbiter_resolution` — Gemini 2.5 Pro tiebreak resolved a disagreement (§1.6 step 3). Payload: `{gemini_answer: any, rationale: str}`

### action (4)

- `action_executed` — input sent to target HWND. Payload: `{scan_codes: list, action_hash: str}`
- `action_dropped_focus` — action dropped because window lost focus (E122). Payload: `{scan_codes: list, last_focus_ts_ns: int}`
- `action_dropped_target_lost` — action dropped because target HWND vanished (E121). Payload: `{scan_codes: list, hwnd: int}`
- `action_repetition_guard_fired` — Amendment A13 action repetition guard triggered W2. Payload: `{action_hash: str, repetition_count: int, window_s: float}`

### replay (3)

Step 4+ scope. Placeholders for the record-replay harness.

- `replay_load` — session loaded for replay. Payload: `{source_session_id: str, frame_count: int}`
- `replay_step_ok` — one replay step advanced. Payload: `{frame_id: str, brain_decision: any}`
- `replay_diff` — semantic diff emitted between baseline and replay. Payload: `{divergence_point: str, baseline: any, replayed: any}`

### session (7)

- `session_start` — daemon accepted the session. Payload: `{adapter: str, task: str, started_ts_wall: str}`
- `session_complete` — normal success. Payload: `{outcome: "success", duration_s: float, total_cost_usd: float}`
- `session_aborted_runaway` — 30-call runaway kill switch fired (§1.4). Payload: `{brain_call_count: int}`
- `session_aborted_perception_unavailable` — Layer 1 backend absence, 3 stale ticks exceeded (Amendment A6). Payload: `{last_error: str}`
- `session_aborted_brain_unavailable` — Layer 3 backend absence. Payload: `{last_error: str}`
- `session_aborted_runaway` — already listed
- `session_aborted_unhandled_exception` — top-level catch-all. Payload: `{error_type: str, traceback: str (scrubbed)}`

## Dual-file routing

Every event lands in `events.jsonl`. Events where `producer == "brain"` AND
`event_type` starts with `wake_` or `brain_` ALSO land in `brain_calls.jsonl`.
This gives you a cheap, pre-filtered stream for:

- v2-T2 skill-compounding metric (reduce brain call counts across repeated runs)
- Cost analysis (sum `payload.cost_usd` across a day)
- Prompt caching hit rate (`sum(cached_system=true) / total`)

## Consumer rules

**Always use `ts_monotonic_ns` for ordering** within a session. `ts_wall`
is for human reading + cross-machine correlation; it can jump backward
on NTP corrections.

**Respect `scrub_secrets()`**. Secrets in `payload` are already redacted
to `sk-ant-REDACTED` before write. Downstream analyzers don't need to
re-scrub, but they MUST NOT log unredacted input (if any sneaks in).

**Always check `schema_version`**. A v2 reader running against a v1
file should detect the version and dispatch to a legacy decoder. The
migration shim scaffolding lives in `gamemind/events/migrations/` but
isn't populated until the first breaking change ships.

**Do not re-order event lines on write**. The writer is single-threaded
(one bg drain thread) and preserves producer-order. Any re-ordering at
read time must be keyed on `ts_monotonic_ns`, not line order.

## Example session trace

```jsonl
{"schema_version":1,"session_id":"abc123","ts_monotonic_ns":100,"ts_wall":"2026-04-11T14:00:00.000+00:00","frame_id":null,"producer":"session","event_type":"session_start","payload":{"adapter":"minecraft","task":"chop 3 oak logs"}}
{"schema_version":1,"session_id":"abc123","ts_monotonic_ns":150,"ts_wall":"2026-04-11T14:00:00.050+00:00","frame_id":"f001","producer":"capture","event_type":"capture_ok","payload":{"backend":"WGC","variance":0.87}}
{"schema_version":1,"session_id":"abc123","ts_monotonic_ns":450,"ts_wall":"2026-04-11T14:00:00.350+00:00","frame_id":"f001","producer":"perception","event_type":"perception_tick","payload":{"frame_age_ms":300.2,"latency_ms":285,"json_ok":true}}
{"schema_version":1,"session_id":"abc123","ts_monotonic_ns":500,"ts_wall":"2026-04-11T14:00:00.400+00:00","frame_id":"f001","producer":"brain","event_type":"wake_w1","payload":{"trigger":"task_start","plan":"approach the oak tree at 2 o'clock and attack","latency_ms":1200,"cost_usd":0.003}}
{"schema_version":1,"session_id":"abc123","ts_monotonic_ns":1800,"ts_wall":"2026-04-11T14:00:01.700+00:00","frame_id":"f005","producer":"action","event_type":"action_executed","payload":{"scan_codes":["W"],"action_hash":"a1b2c3"}}
```

Same session's `brain_calls.jsonl`:

```jsonl
{"schema_version":1,"session_id":"abc123","ts_monotonic_ns":500,"ts_wall":"2026-04-11T14:00:00.400+00:00","frame_id":"f001","producer":"brain","event_type":"wake_w1","payload":{"trigger":"task_start","plan":"approach the oak tree at 2 o'clock and attack","latency_ms":1200,"cost_usd":0.003}}
```

Just the brain wake — everything else is events-only.
