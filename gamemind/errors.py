"""GameMind error hierarchy — Tier 2 structured messages per autoplan §10.2.E.

Each exception has an error code (E1XX) and a structured message format:
  CODE: <primary label>
    cause: <what happened>
    fix:   <how to recover>
    docs:  docs/errors.md#<code>

Phase C Step 1 introduces 22 classes covering capture, perception, brain,
adapter loading, verification, and input paths. All errors that propagate
to the CLI pass through `format_user_message()` for operator-facing output.

Reference: docs/final-design.md §10.2.E Error & Rescue Registry,
Amendment A6 Backend Absence Recovery (§10.6.C).
"""

from __future__ import annotations


class GameMindError(Exception):
    """Base class for all GameMind exceptions.

    Subclasses MUST define:
      - code: class-level error code like "E101"
      - label: one-line primary message
      - fix:   one-line remediation hint
    """

    code: str = "E000"
    label: str = "Unspecified GameMind error"
    fix: str = "See docs/errors.md for diagnostic steps."

    def __init__(self, cause: str = "", **context: object) -> None:
        self.cause = cause
        self.context = context
        super().__init__(self.format_user_message())

    def format_user_message(self) -> str:
        ctx = ""
        if self.context:
            ctx = "\n  context: " + ", ".join(f"{k}={v!r}" for k, v in self.context.items())
        return (
            f"{self.code}: {self.label}\n"
            f"  cause: {self.cause or 'unspecified'}\n"
            f"  fix:   {self.fix}\n"
            f"  docs:  docs/errors.md#{self.code.lower()}"
            f"{ctx}"
        )


# ---------- Layer 0: Capture errors (WGC / DXGI / windows) --------------


class WGCInitError(GameMindError):
    code = "E101"
    label = "Windows Graphics Capture backend failed to initialize"
    fix = "Ensure Windows 10 build 1903+ and DXGI driver present; selector will fall back to DXGI."


class DXGIInitError(GameMindError):
    code = "E102"
    label = "DXGI Desktop Duplication backend failed to initialize"
    fix = "Check GPU driver and that no other DXGI Duplication client is active."


class DXGIFrameGrabError(GameMindError):
    code = "E103"
    label = "DXGI frame grab raced or produced no frame"
    fix = "Retry with jitter; if persistent, swap to WGC if the target is windowed."


class BlackFrameThresholdError(GameMindError):
    code = "E104"
    label = "Capture backend returned black frames above threshold"
    fix = "Selector will swap backends. If both return black, the game may be minimized."


class WindowNotFoundError(GameMindError):
    code = "E105"
    label = "No matching game window (HWND) found"
    fix = "Launch the target game and bring it to focus, then retry."


# ---------- Layer 1: Perception errors (Ollama / VLM) ------------------


class OllamaConnectionError(GameMindError):
    code = "E106"
    label = "Cannot reach Ollama at the configured host"
    fix = "Run `ollama serve` in a separate terminal; default host is http://127.0.0.1:11434."


class OllamaModelMissingError(GameMindError):
    code = "E107"
    label = "Ollama does not have the required model loaded"
    fix = "Run `ollama pull gemma4:26b-a4b-it-q4_K_M` and verify with `ollama list`."


class OllamaOOMError(GameMindError):
    code = "E108"
    label = "Ollama ran out of GPU memory during inference"
    fix = "Close other GPU-heavy processes or drop to a smaller model via --model."


class OllamaTimeoutError(GameMindError):
    code = "E109"
    label = "Ollama inference exceeded timeout"
    fix = "Check 5090 utilization; consider reducing num_ctx or frame resolution."


class PerceptionJSONError(GameMindError):
    code = "E110"
    label = "Perception model response failed JSON parse"
    fix = "Retry once at temperature=0; if sustained, check <think> tag leakage (model variant)."


class PerceptionBacklogError(GameMindError):
    code = "E111"
    label = "Perception tick latency p90 exceeded freshness budget"
    fix = "Enforce latest-wins queue per Amendment A1; drop-oldest policy is in gamemind/perception/daemon.py."


# ---------- Layer 3: Brain errors (Anthropic / Gemini fallback) ---------


class AnthropicRateLimitError(GameMindError):
    code = "E112"
    label = "Anthropic API 429 rate limit"
    fix = "Exponential backoff 3x then abort session; check Max Plan usage."


class AnthropicServiceError(GameMindError):
    code = "E113"
    label = "Anthropic API 5xx service error"
    fix = "Retry once; if persistent, check https://status.anthropic.com and consider D3 Gemini fallback."


class AnthropicTimeoutError(GameMindError):
    code = "E114"
    label = "Anthropic API request exceeded timeout"
    fix = "Reduce prompt size or retry; consider caching system prompt (cache_system=True)."


class BrainResponseError(GameMindError):
    code = "E115"
    label = "Brain response failed schema validation"
    fix = "Check the prompt template against `docs/protocols.md` LLMResponse schema."


class AnthropicSafetyRefusalError(GameMindError):
    code = "E116"
    label = "Anthropic safety system refused the request"
    fix = "Adjust prompt to avoid unsafe content; log the refusal to events.jsonl."


# ---------- Layer 6: Adapter loading errors ----------------------------


class AdapterYAMLParseError(GameMindError):
    code = "E117"
    label = "Adapter YAML failed to parse"
    fix = "Validate with `gamemind adapter validate <path>` once Step 3 ships; check YAML syntax."


class AdapterPyInjectionError(GameMindError):
    code = "E118"
    label = "Adapter contains forbidden Python code injection"
    fix = "Adapters must be pure YAML. Remove any !!python/... tags."


class AdapterSchemaError(GameMindError):
    code = "E119"
    label = "Adapter does not match the expected schema"
    fix = "Check schema_version, required fields, and type constraints in docs/adapter-schema.md."


class AdapterPathTraversalError(GameMindError):
    code = "E120"
    label = "Adapter referenced a path outside the project root"
    fix = (
        "All adapter-referenced paths must be relative to the project root; symlinks are rejected."
    )


# ---------- Layer 4: Input errors --------------------------------------


class InputTargetLostError(GameMindError):
    code = "E121"
    label = "Input target window was closed or became invalid"
    fix = "Session is aborted. Relaunch the game and start a new session."


class InputFocusError(GameMindError):
    code = "E122"
    label = "Input dropped because target window lost focus"
    fix = "Ensure the game window stays foreground; avoid alt-tab during active sessions."


# ---------- Verify layer -----------------------------------------------


class TemplateAssetMissingError(GameMindError):
    code = "E123"
    label = "Verify predicate referenced a template asset that does not exist"
    fix = "Check the adapter's template_match references; assets live under adapters/<name>/templates/."


__all__ = [
    "AdapterPathTraversalError",
    "AdapterPyInjectionError",
    "AdapterSchemaError",
    "AdapterYAMLParseError",
    "AnthropicRateLimitError",
    "AnthropicSafetyRefusalError",
    "AnthropicServiceError",
    "AnthropicTimeoutError",
    "BlackFrameThresholdError",
    "BrainResponseError",
    "DXGIFrameGrabError",
    "DXGIInitError",
    "GameMindError",
    "InputFocusError",
    "InputTargetLostError",
    "OllamaConnectionError",
    "OllamaModelMissingError",
    "OllamaOOMError",
    "OllamaTimeoutError",
    "PerceptionBacklogError",
    "PerceptionJSONError",
    "TemplateAssetMissingError",
    "WGCInitError",
    "WindowNotFoundError",
]
