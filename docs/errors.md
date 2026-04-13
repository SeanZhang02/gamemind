# GameMind Error Reference (E101–E123)

Every exception raised by `gamemind/errors.py` follows the Tier-2 structured
message format from the DX subagent review: `CODE: primary label / cause /
fix / docs link`. This file is the operator-facing reference you land on when
a daemon error prints `docs/errors.md#e106`.

All errors inherit from `gamemind.errors.GameMindError` — base class with
`code`, `label`, `fix` class attributes and a `format_user_message()` method
that builds the structured block.

## Quick table

| Code | Class | Layer | Severity |
|---|---|---|---|
| E101 | `WGCInitError` | Layer 0 Capture | Recoverable (selector falls through to DXGI) |
| E102 | `DXGIInitError` | Layer 0 Capture | Fatal for exclusive-fullscreen games |
| E103 | `DXGIFrameGrabError` | Layer 0 Capture | Recoverable (retry with jitter) |
| E104 | `BlackFrameThresholdError` | Layer 0 Capture | Recoverable once, fatal if both backends produce black |
| E105 | `WindowNotFoundError` | Layer 0 Capture | Fatal for the session |
| E106 | `OllamaConnectionError` | Layer 1 Perception | Recoverable (Amendment A6: up to 3 stale ticks) |
| E107 | `OllamaModelMissingError` | Layer 1 Perception | Fatal at startup |
| E108 | `OllamaOOMError` | Layer 1 Perception | Fatal for the session |
| E109 | `OllamaTimeoutError` | Layer 1 Perception | Recoverable (drop frame, continue) |
| E110 | `PerceptionJSONError` | Layer 1 Perception | Recoverable (retry at temp=0) |
| E111 | `PerceptionBacklogError` | Layer 1 Perception | Fatal if sustained (Amendment A1 freshness violation) |
| E112 | `AnthropicRateLimitError` | Layer 3 Brain | Recoverable (backoff 3×, then abort) |
| E113 | `AnthropicServiceError` | Layer 3 Brain | Recoverable (retry once, then abort) |
| E114 | `AnthropicTimeoutError` | Layer 3 Brain | Recoverable (prompt trim + retry) |
| E115 | `BrainResponseError` | Layer 3 Brain | Recoverable (parse with fallback schema) |
| E116 | `AnthropicSafetyRefusalError` | Layer 3 Brain | Fatal for the session (log + abort) |
| E117 | `AdapterYAMLParseError` | Layer 6 Adapter | Fatal at startup |
| E118 | `AdapterPyInjectionError` | Layer 6 Adapter | Fatal at startup (Rule 2 violation) |
| E119 | `AdapterSchemaError` | Layer 6 Adapter | Fatal at startup |
| E120 | `AdapterPathTraversalError` | Layer 6 Adapter | Fatal at startup (Amendment A9 violation) |
| E121 | `InputTargetLostError` | Layer 4 Action | Fatal for the session |
| E122 | `InputFocusError` | Layer 4 Action | Recoverable (log drop, continue) |
| E123 | `TemplateAssetMissingError` | Verify engine | Recoverable (treat predicate as unknown) |

## Detailed reference

### E101 — `WGCInitError`

**Label**: Windows Graphics Capture backend failed to initialize.
**Common cause**: WGC runtime not present (Windows 10 build 1903+ required) or DXGI driver missing.
**Fix**: Ensure Windows 10 build 1903+ and a current GPU driver. The capture selector will fall back to DXGI automatically — this error usually logs a doctor warning rather than aborting the session.
**When recoverable**: Yes — Amendment A1 Perception Freshness Contract treats this as a backend swap event, not a session-level failure.

### E102 — `DXGIInitError`

**Label**: DXGI Desktop Duplication backend failed to initialize.
**Common cause**: GPU driver issue, or another DXGI Duplication client (e.g. a streaming app) holds an exclusive session.
**Fix**: Check GPU driver. Close other capture clients (OBS Studio, Parsec, Moonlight). If running under Remote Desktop or a virtual display, DXGI may be unavailable — use WGC-only mode.
**When recoverable**: If WGC is working, the selector sticks with WGC. If BOTH fail, session aborts with `outcome: capture_unavailable`.

### E103 — `DXGIFrameGrabError`

**Label**: DXGI frame grab raced or produced no frame.
**Common cause**: Desktop Duplication throttled by display scaling changes, resolution switch, or a transient GPU hiccup.
**Fix**: Retry with jitter (Amendment A1 freshness queue handles this transparently — the tick is marked failed and a fresh capture is attempted). If persistent, swap to WGC if the target is windowed.
**When recoverable**: Yes — latest-wins queue keeps the daemon running even with sporadic grab errors.

### E104 — `BlackFrameThresholdError`

**Label**: Capture backend returned black frames above threshold.
**Common cause**: Game is minimized, the target window is occluded, or display is locked.
**Fix**: The selector swaps WGC → DXGI after N consecutive black frames (default 5, see `gamemind/capture/selector.py`). If BOTH backends produce black frames, the session aborts — the game is likely minimized.
**When recoverable**: Selector handles first-tier fallback automatically.

### E105 — `WindowNotFoundError`

**Label**: No matching game window (HWND) found.
**Common cause**: Target game isn't running, or the `--window-title` filter didn't match any HWND.
**Fix**: Launch the target game and bring it to focus. Use `gamemind doctor --capture` to list visible HWNDs. Use `--window-title "Minecraft*"` to filter when multiple candidate windows are open.
**When recoverable**: Session-level retry only — no mid-session recovery.

### E106 — `OllamaConnectionError`

**Label**: Cannot reach Ollama at the configured host.
**Common cause**: `ollama serve` not running, wrong host/port, or Ollama crashed mid-session.
**Fix**: Run `ollama serve` in a separate terminal. Verify with `curl http://127.0.0.1:11434/api/tags`. Set `GAMEMIND_OLLAMA_HOST` env var if using a non-default host.
**When recoverable**: Amendment A6 allows up to 3 consecutive stale ticks with reconnect attempts (1s → 3s → 9s exponential backoff). 4th failure aborts the session with `outcome: perception_unavailable`.

### E107 — `OllamaModelMissingError`

**Label**: Ollama does not have the required model loaded.
**Common cause**: Model hasn't been pulled yet, or was removed.
**Fix**: Run `ollama pull gemma4:26b-a4b-it-q4_K_M`. Verify with `ollama list`. Override via `GAMEMIND_OLLAMA_MODEL` env var if using a non-default model.
**When recoverable**: Fatal at daemon startup — `/healthz` returns `degraded` until the model is pulled.

### E108 — `OllamaOOMError`

**Label**: Ollama ran out of GPU memory during inference.
**Common cause**: Another GPU-heavy process is competing for VRAM, or the model is too large for available VRAM (need ~6.1GB for `gemma4:26b-a4b-it-q4_K_M`).
**Fix**: Close other GPU-heavy processes (games, video editors, CUDA workloads). Consider swapping to a smaller model variant if your GPU has <8GB VRAM.
**When recoverable**: Fatal for the current session — OOM usually means the next inference will also OOM.

### E109 — `OllamaTimeoutError`

**Label**: Ollama inference exceeded timeout.
**Common cause**: Model is slow under load, or the prompt is unexpectedly long.
**Fix**: Check GPU utilization. Consider reducing `num_ctx` via the `OllamaBackend(num_ctx=...)` constructor parameter (Amendment A15). If running at `num_ctx >8192`, ensure `explicit_long_context=True` is set.
**When recoverable**: Drop frame, continue — Amendment A1 freshness queue treats the tick as failed.

### E110 — `PerceptionJSONError`

**Label**: Perception model response failed JSON parse.
**Common cause**: Model occasionally returns non-JSON text, especially when the prompt is ambiguous. Also happens if `<think>` tags leak from a thinking-variant model.
**Fix**: Retry once at `temperature=0`. If sustained, check for `<think>` tag leakage (model variant issue). The `think=False` API param is set defensively to suppress CoT on models that support it.
**When recoverable**: First retry is free; two consecutive parse failures on the same tick mark it as failed.

### E111 — `PerceptionBacklogError`

**Label**: Perception tick latency p90 exceeded freshness budget.
**Common cause**: Layer 1 is saturating — p90 `frame_age_at_action` > 1000ms (Amendment A1 default). The perception loop is falling behind the 2-3Hz tick budget.
**Fix**: Enforce latest-wins queue (already implemented in `gamemind/perception/freshness.py`). Check Ollama GPU utilization. Consider reducing tick rate via the adapter `perception.tick_hz` field (default 2.0). If sustained, the session aborts with `outcome: perception_backlog`.
**When recoverable**: Drop-oldest policy keeps the daemon running, but sustained drops (>10% over 10s) raise this error and may abort.

### E112 — `AnthropicRateLimitError`

**Label**: Anthropic API 429 rate limit.
**Common cause**: Exceeded tier-based rate limits, or Max Plan budget envelope.
**Fix**: Amendment A6 specifies exponential backoff (up to 60s cap), 3 retries per wake, then abort with `outcome: brain_rate_limited`. Check Max Plan usage at https://console.anthropic.com/.
**When recoverable**: Yes, for transient 429s — the Anthropic SDK handles retry automatically via `max_retries=2`.

### E113 — `AnthropicServiceError`

**Label**: Anthropic API 5xx service error.
**Common cause**: Transient Anthropic infrastructure issue.
**Fix**: Retry once with 2s delay (per Amendment A6). If persistent, check https://status.anthropic.com and consider D3 Gemini fallback.
**When recoverable**: Yes, for single-shot 5xx.

### E114 — `AnthropicTimeoutError`

**Label**: Anthropic API request exceeded timeout.
**Common cause**: Long prompts on slow paths, or network latency.
**Fix**: Retry once with a prompt-trimmed request (drop oldest context). Consider enabling prompt caching via `cache_system=True` to reduce input token count on subsequent wakes.
**When recoverable**: Yes, per Amendment A6 policy.

### E115 — `BrainResponseError`

**Label**: Brain response failed schema validation.
**Common cause**: Claude returned text that doesn't match the expected JSON schema — typically a prompt template issue.
**Fix**: Check the prompt template against `docs/adapter-schema.md` or `docs/protocols.md` for the expected `LLMResponse` shape. The template may need to specify the schema more explicitly.
**When recoverable**: Log + session continues; the specific wake is treated as failed.

### E116 — `AnthropicSafetyRefusalError`

**Label**: Anthropic safety system refused the request.
**Common cause**: Prompt triggered safety filters — very unlikely on game content but possible with adversarial adapter data.
**Fix**: Adjust adapter `world_facts` to remove any content the safety system objected to. Log the refusal to `events.jsonl`.
**When recoverable**: Fatal for the session.

### E117 — `AdapterYAMLParseError`

**Label**: Adapter YAML failed to parse.
**Common cause**: Syntax error in the YAML file (indentation, unbalanced quotes, invalid unicode).
**Fix**: Validate with `gamemind adapter validate <path>` (Step 3 scope). Check YAML syntax manually. Use an editor with YAML highlighting.
**When recoverable**: Fatal at daemon startup.

### E118 — `AdapterPyInjectionError`

**Label**: Adapter contains forbidden Python code injection.
**Common cause**: Adapter YAML contains `!!python/object` or similar Python tags. This is a Design Rule 2 violation.
**Fix**: Remove all `!!python/...` tags. Adapters must be pure YAML data. See `docs/adapter-schema.md` for the allowed schema.
**When recoverable**: Never — this is a security boundary.

### E119 — `AdapterSchemaError`

**Label**: Adapter does not match the expected schema.
**Common cause**: Unknown field, wrong type, missing required field, or `schema_version` mismatch.
**Fix**: Check `schema_version`, required fields (`display_name`, `actions`, `goal_grammars`), and type constraints in `docs/adapter-schema.md`. The pydantic error messages are the canonical source of truth.
**When recoverable**: Fatal at daemon startup.

### E120 — `AdapterPathTraversalError`

**Label**: Adapter referenced a path outside the project root.
**Common cause**: Adapter file is outside `adapters/` OR references an image/template path via `..` / symlink / absolute path.
**Fix**: All adapter-referenced paths must be relative to the project root's `adapters/` directory. Symlinks are rejected. Use paths like `templates/tree.png` (relative to the adapter file's directory).
**When recoverable**: Never — Amendment A9 security boundary.

### E121 — `InputTargetLostError`

**Label**: Input target window was closed or became invalid.
**Common cause**: The player closed the game mid-session.
**Fix**: Session aborts with `outcome: input_target_lost`. Relaunch the game and start a new session.
**When recoverable**: Session-level retry only.

### E122 — `InputFocusError`

**Label**: Input dropped because target window lost focus.
**Common cause**: Alt-tab, Windows notification stole focus, screensaver activated.
**Fix**: Ensure the game window stays in the foreground. Disable Windows notifications during sessions. `gamemind` logs the dropped action and continues.
**When recoverable**: Yes — per-action log, no session abort.

### E123 — `TemplateAssetMissingError`

**Label**: Verify predicate referenced a template asset that does not exist.
**Common cause**: Adapter's `template_match: templates/foo.png` but `adapters/<name>/templates/foo.png` doesn't exist.
**Fix**: Create the template asset. Use `gamemind adapter validate <path>` to list missing assets before session start.
**When recoverable**: Predicate is treated as unknown; verify tiers fall through to `vision_critic` tier.

## Integration notes

- **Never swallow these errors in implementation code**. Catch and re-raise with context, or let them bubble up to the session handler which maps them to `runs/<session>/errors.jsonl` lines.
- **Every error automatically passes through `scrub_secrets()`** (Amendment A10) — API keys won't leak even if they appear in a traceback.
- **`runs/<session>/errors.jsonl` format**: one line per error, JSON-encoded, matching the Amendment A2 envelope schema with `producer: session`, `event_type: session_aborted_*`, and the full formatted error message in `payload.error`.
