# Protocol Reference (LLMBackend / CaptureBackend / InputBackend)

Per Amendment A12, these three Protocols are **frozen** on day 1 of
Phase C so pragmatist (and anyone else writing a backend implementation)
has a stable contract. The Protocols are defined in the actual code;
this doc is the human-readable reference + implementation guide.

## LLMBackend

**Source**: `gamemind/brain/backend.py::LLMBackend`

Satisfied by:
- `gamemind/perception/ollama_backend.py::OllamaBackend` (Layer 1 perception)
- `gamemind/brain/anthropic_backend.py::AnthropicBackend` (Layer 3 brain)
- (Future) `gemini_backend.py` for D3 fallback

### Signature

```python
class LLMBackend(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        cache_system: bool,
        request_id: str,
        emit_event: bool = True,
    ) -> LLMResponse: ...
```

### LLMResponse

```python
@dataclass
class LLMResponse:
    text: str                          # raw response string (never None; "" on error)
    parsed_json: dict[str, Any] | None # parsed dict if valid JSON; None otherwise
    prompt_tokens: int                 # input tokens (incl. cache hits)
    completion_tokens: int             # output tokens
    cost_estimate_usd: float           # per-backend pricing; 0.0 for local
    latency_ms: float                  # wall clock, start→return
    request_id: str                    # echoed from caller unchanged
    cached_system: bool                # True iff cache hit this call
    backend_meta: dict[str, Any]       # escape hatch for backend internals
```

### Implementation rules (non-negotiable)

1. **Honor `temperature` and `max_tokens` exactly** — no silent override. Exception: Anthropic adaptive thinking works best at `temperature=1.0`; callers should not pass anything else, but the backend should still pass the value through.
2. **Echo `request_id` on the response unchanged**. Used for correlation with `brain_calls.jsonl` (Amendment A2).
3. **NEVER raise from network or API errors**. Return `LLMResponse(text="", parsed_json=None, backend_meta={"error": "...", "error_msg": "..."})` instead. Caller routes recovery per Amendment A6 Backend Absence Recovery.
4. **`emit_event=True` by default**. Layer 3 wake events belong in `brain_calls.jsonl`. Layer 1 continuous perception should set `emit_event=False` to avoid log explosion (2-3 Hz × 10 minutes = 1200+ events per task).
5. **Caller-errors ARE raise-worthy**. Invalid arguments (bad `max_tokens`, missing required field) should raise `ValueError` or `TypeError` — not wrap as backend error. The distinction: network-level errors return structured, caller-level errors raise.

### backend_meta conventions

Optional fields — callers MUST NOT depend on specific keys but can read them for diagnostics:

- `backend: str` — backend name (`"ollama"`, `"anthropic"`, `"gemini"`)
- `error: str` — error code if something failed (`"rate_limit"`, `"connection_error"`, etc.)
- `error_msg: str` — human-readable error message (scrubbed of secrets)
- `model: str` — which model variant was used
- `stop_reason: str` — backend-specific stop reason
- `think_leaked: bool` — (Ollama) `<think>` tag detected in response
- `cache_read_input_tokens: int`, `cache_creation_input_tokens: int`, `uncached_input_tokens: int` — (Anthropic) cache token accounting
- `total_duration_ns: int`, `eval_count: int` — (Ollama) timing internals

### Example: AnthropicBackend usage

```python
from gamemind.brain import AnthropicBackend

backend = AnthropicBackend(
    system="You are an agent playing Minecraft Java Edition...",
    model="claude-opus-4-6",
)

result = backend.chat(
    messages=[{"role": "user", "content": "What should I do next?"}],
    temperature=1.0,        # adaptive thinking expects temperature=1
    max_tokens=16000,
    cache_system=True,      # reuse system prompt cache across wakes
    request_id="wake-w1-0001",
)

if result.backend_meta.get("error"):
    # Backend absence — route per Amendment A6
    ...
elif result.parsed_json:
    plan = result.parsed_json
    cost = result.cost_estimate_usd
    ...
```

---

## CaptureBackend

**Source**: `gamemind/capture/backend.py::CaptureBackend`

Satisfied by:
- `gamemind/capture/wgc_backend.py::WGCBackend` (primary, Windows Graphics Capture)
- `gamemind/capture/dxgi_backend.py::DXGIBackend` (fallback, Desktop Duplication)

### Signature

```python
class CaptureBackend(Protocol):
    def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult: ...
    def liveness(self) -> bool: ...
```

### CaptureResult

```python
@dataclass
class CaptureResult:
    frame_bytes: bytes           # WEBP-encoded (quality 95)
    frame_age_ms: float          # monotonic_now - capture_ts (Amendment A1)
    capture_backend: Literal["WGC", "DXGI"]
    variance: float              # black-frame heuristic input
    width: int
    height: int
```

### Implementation rules

1. **`capture()` must respect `timeout_ms`**. No indefinite blocking. If the backend can't produce a frame within the budget, raise `gamemind.errors.DXGIFrameGrabError` (or equivalent) and let the selector handle it.
2. **Always populate `frame_age_ms`** at result construction time. This is the Amendment A1 freshness contract — downstream consumers trust this field.
3. **`frame_bytes` is WEBP-encoded**. Raw RGB is too large (6GB/10min at 2Hz). Quality 95 is near-lossless for gameplay frames.
4. **`variance` must be computable at zero cost**. Downstream selector uses it for black-frame detection — compute once during capture, not after.
5. **`liveness()` is cheap**. Called on every daemon `/healthz` probe. No slow operations — return a cached health bool updated by the capture loop.

### CaptureBackend Protocol check

Use the Protocol for type checking without requiring inheritance:

```python
from gamemind.capture import CaptureBackend
from gamemind.capture.wgc_backend import WGCBackend

backend: CaptureBackend = WGCBackend()  # type-checks without explicit inheritance
```

---

## InputBackend

**Source**: `gamemind/input/backend.py::InputBackend` (Phase C Step 2 scope — not yet shipped)

Planned implementations:
- `gamemind/input/pydirectinput_backend.py::PyDirectInputBackend` — scan codes via `SendInput`

### Signature (planned)

```python
class InputBackend(Protocol):
    def send_scan_codes(
        self,
        hwnd: int,
        scan_code_sequence: list[ScanCode],
    ) -> InputResult: ...
```

### ScanCode / InputResult (planned)

```python
@dataclass(frozen=True)
class ScanCode:
    code: str                    # "W", "Space", "LeftShift", "MouseLeft", ...
    down: bool                   # True = press, False = release
    hold_ms: float = 0.0         # hold duration before release; 0 = instant

@dataclass
class InputResult:
    executed: bool
    dropped_reason: Literal["focus_lost", "target_closed", "rate_limit"] | None
    action_hash: str             # hash of the scan code sequence for A13 guard
```

### Implementation rules (planned)

1. **Scan codes only, never virtual key codes**. Minecraft (and most games) only receive SendInput correctly with scan codes; VK codes are silently dropped.
2. **Honor focus state**. If the target window doesn't have keyboard focus, set `dropped_reason="focus_lost"` and return without sending. Amendment A13 action repetition guard depends on this signal.
3. **Populate `action_hash`** with a stable hash over the scan code sequence. The hash must be deterministic — same sequence → same hash across runs. SHA256 of the sequence's repr is fine.
4. **Never raise from input failures**. Same rule as LLMBackend — return structured error so the daemon can route recovery.

---

## Common patterns

### Dependency injection for testing

All three Protocols are structural, not nominal. Tests can inject
fake backends without importing the real one:

```python
class FakeBackend:
    def chat(self, messages, *, temperature, max_tokens, cache_system, request_id, emit_event=True):
        return LLMResponse(text="fake", parsed_json=None, prompt_tokens=0,
                          completion_tokens=0, cost_estimate_usd=0.0,
                          latency_ms=0.0, request_id=request_id,
                          cached_system=False, backend_meta={})

def some_caller(backend: LLMBackend):
    result = backend.chat(...)

some_caller(FakeBackend())  # ✓ satisfies Protocol by structure
```

### Never subclass the Protocol class

Protocols are for type hints. Implementations should NOT inherit from `LLMBackend`, `CaptureBackend`, or `InputBackend` — just implement the methods and let duck typing handle it.

### Never extend the Protocol with new required methods

If you need a new method, add it as an optional method (`hasattr()` check at call site) or create a separate Protocol (`ExtendedLLMBackend`). Breaking existing Protocol signatures would force every implementation to update — which defeats the point of Amendment A12 freezing them.

### backend_meta escape hatch

Every backend has access to `backend_meta` for diagnostics the caller MUST NOT depend on but MAY read. Use this for:

- Backend-specific timing internals (`total_duration_ns`)
- Cache accounting that varies per backend
- Non-standard error codes
- Model variant disambiguation (e.g. `gemma4:26b-a4b-it-q4_K_M` vs other quantization variants)

**Never put required fields in `backend_meta`**. If the caller needs it, promote it to the typed dataclass.
