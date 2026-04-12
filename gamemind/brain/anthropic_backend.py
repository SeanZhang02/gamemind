"""AnthropicBackend — Layer 3 brain implementation of LLMBackend.

Uses the official `anthropic` Python SDK per the claude-api skill guidance:

- Default model `claude-opus-4-6` (configurable)
- Adaptive thinking (`thinking: {type: "adaptive"}`) — Claude decides depth
- Prompt caching on system prompt (5-minute TTL via `cache_control: {ephemeral}`)
- Streaming via `client.messages.stream().get_final_message()` — prevents
  HTTP timeout on long outputs without needing per-event handlers
- Typed exceptions (`anthropic.RateLimitError`, etc.) — no string matching
- `chat()` returns LLMResponse with error in backend_meta on any failure;
  never raises from network/API errors (caller routes recovery per
  Amendment A6 Backend Absence Recovery)

ANTHROPIC_API_KEY must be set in the env (Amendment A10). The backend
refuses to initialize if the env var is missing at construction time.
"""

from __future__ import annotations

import os
import time
from typing import Any

import anthropic

from gamemind.brain.backend import LLMResponse

DEFAULT_MODEL = "claude-opus-4-6"
ENV_API_KEY = "ANTHROPIC_API_KEY"

_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (5.0 / 1_000_000, 25.0 / 1_000_000),
    "claude-sonnet-4-6": (3.0 / 1_000_000, 15.0 / 1_000_000),
    "claude-sonnet-4-5": (3.0 / 1_000_000, 15.0 / 1_000_000),
    "claude-haiku-4-5": (1.0 / 1_000_000, 5.0 / 1_000_000),
}
_DEFAULT_INPUT_COST = 5.0 / 1_000_000
_DEFAULT_OUTPUT_COST = 25.0 / 1_000_000


def _estimate_cost_usd(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
) -> float:
    """Estimate USD cost from an Anthropic response's usage fields.

    Dispatches to the correct price table per model. Falls back to
    Opus 4.6 pricing for unknown models (fail-safe: overestimates).
    """
    input_cost, output_cost = _PRICING.get(model, (_DEFAULT_INPUT_COST, _DEFAULT_OUTPUT_COST))
    cache_read_cost = input_cost * 0.1
    cache_write_cost = input_cost * 1.25
    return (
        input_tokens * input_cost
        + output_tokens * output_cost
        + cache_read_input_tokens * cache_read_cost
        + cache_creation_input_tokens * cache_write_cost
    )


class AnthropicBackend:
    """Layer 3 brain backend using the Anthropic Python SDK.

    Usage:
        backend = AnthropicBackend(
            system="You are an agent playing Minecraft...",
            model="claude-opus-4-6",
        )
        result = backend.chat(
            messages=[{"role": "user", "content": "What should I do next?"}],
            temperature=1.0,       # adaptive thinking requires temperature=1
            max_tokens=16000,
            cache_system=True,     # cache the system prompt for reuse
            request_id="wake-w1-0001",
        )

    The system prompt is set at construction (agent-level), not per-call —
    prompt caching works best on stable prefixes. Per-call content goes in
    `messages`. If `cache_system=True` and the system prompt is at least
    4096 tokens (Opus 4.6 minimum), prompt caching kicks in and subsequent
    calls read cached tokens at ~10% cost.

    Errors never propagate out of `chat()`:
      - anthropic.RateLimitError       → backend_meta["error"]="rate_limit"
      - anthropic.APIStatusError (5xx) → backend_meta["error"]="service_error"
      - anthropic.APIConnectionError   → backend_meta["error"]="connection_error"
      - anthropic.BadRequestError      → backend_meta["error"]="bad_request"
      - anthropic.AuthenticationError  → backend_meta["error"]="auth_error"
      - anthropic.APIError (generic)   → backend_meta["error"]="api_error"
    """

    def __init__(
        self,
        *,
        system: str,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_retries: int = 2,
        timeout_s: float = 60.0,
    ) -> None:
        # Amendment A10: ANTHROPIC_API_KEY must be in env, not hard-coded.
        # Accept an explicit api_key for test injection, but warn via the
        # env check if neither is set.
        effective_key = api_key or os.environ.get(ENV_API_KEY)
        if not effective_key:
            raise ValueError(
                f"{ENV_API_KEY} env var not set and no explicit api_key provided. "
                f"Per Amendment A10, the key must be loaded from the environment. "
                f"Run: export {ENV_API_KEY}=sk-ant-..."
            )
        self.system = system
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=effective_key,
            max_retries=max_retries,
            timeout=timeout_s,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        cache_system: bool,
        request_id: str,
        emit_event: bool = True,  # noqa: ARG002 — events writer hookup in later iter
    ) -> LLMResponse:
        """Call Claude via streaming + get_final_message for timeout safety.

        Uses adaptive thinking per claude-api skill defaults. temperature
        is accepted for Protocol compatibility but adaptive thinking works
        best at temperature=1.0 — callers should not override unless they
        know what they're doing.

        `cache_system=True` wraps the system prompt in a cached text block;
        Anthropic's prefix-match caching returns subsequent system prompts
        at ~10% cost when the prompt is ≥4096 tokens (Opus 4.6 minimum).
        """
        t0 = time.perf_counter()

        # Build the system block. With cache_system=True, wrap as a cached
        # text block; otherwise pass as a raw string (simpler for short
        # prompts that won't hit the 4096-token cache minimum anyway).
        system_param: str | list[dict[str, Any]]
        if cache_system:
            system_param = [
                {
                    "type": "text",
                    "text": self.system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = self.system

        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_param,
                messages=messages,
                thinking={"type": "adaptive"},
                temperature=temperature,
            ) as stream:
                final_message = stream.get_final_message()

            latency_ms = (time.perf_counter() - t0) * 1000.0

            # Extract text from the content blocks. Opus 4.6 may return
            # thinking blocks + text blocks; we concatenate all text blocks
            # for LLMResponse.text. Callers that want thinking blocks can
            # read backend_meta["content_blocks"].
            text_parts: list[str] = []
            for block in final_message.content:
                if block.type == "text":
                    text_parts.append(block.text)
            text = "".join(text_parts)

            # Best-effort JSON parse. Strip markdown code fences if present
            # (Claude sometimes wraps JSON in ```json ... ``` despite "ONLY JSON" instructions).
            parsed_json: dict[str, Any] | None = None
            stripped = text.strip()
            if stripped.startswith("```"):
                lines = stripped.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                stripped = "\n".join(lines).strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    import json  # noqa: PLC0415

                    loaded = json.loads(stripped)
                    if isinstance(loaded, dict):
                        parsed_json = loaded
                except (ValueError, TypeError):
                    pass

            usage = final_message.usage
            input_tokens = usage.input_tokens or 0
            output_tokens = usage.output_tokens or 0
            cache_read_input_tokens = getattr(usage, "cache_read_input_tokens", None) or 0
            cache_creation_input_tokens = getattr(usage, "cache_creation_input_tokens", None) or 0

            cost_usd = _estimate_cost_usd(
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
            )

            return LLMResponse(
                text=text,
                parsed_json=parsed_json,
                prompt_tokens=input_tokens + cache_read_input_tokens + cache_creation_input_tokens,
                completion_tokens=output_tokens,
                cost_estimate_usd=cost_usd,
                latency_ms=latency_ms,
                request_id=request_id,
                cached_system=cache_read_input_tokens > 0,
                backend_meta={
                    "backend": "anthropic",
                    "model": self.model,
                    "stop_reason": final_message.stop_reason,
                    "cache_read_input_tokens": cache_read_input_tokens,
                    "cache_creation_input_tokens": cache_creation_input_tokens,
                    "uncached_input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )

        except anthropic.RateLimitError as exc:
            return self._error_response(
                request_id=request_id,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error="rate_limit",
                error_msg=str(exc),
            )
        except anthropic.AuthenticationError as exc:
            return self._error_response(
                request_id=request_id,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error="auth_error",
                error_msg=str(exc),
            )
        except anthropic.BadRequestError as exc:
            return self._error_response(
                request_id=request_id,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error="bad_request",
                error_msg=str(exc),
            )
        except anthropic.APIStatusError as exc:
            error_type = "service_error" if exc.status_code >= 500 else "api_error"
            return self._error_response(
                request_id=request_id,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error=error_type,
                error_msg=str(exc),
            )
        except anthropic.APIConnectionError as exc:
            return self._error_response(
                request_id=request_id,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error="connection_error",
                error_msg=str(exc),
            )
        except anthropic.APIError as exc:
            return self._error_response(
                request_id=request_id,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                error="api_error",
                error_msg=str(exc),
            )

    @staticmethod
    def _error_response(
        *,
        request_id: str,
        latency_ms: float,
        error: str,
        error_msg: str,
    ) -> LLMResponse:
        """Build an LLMResponse representing a backend error."""
        return LLMResponse(
            text="",
            parsed_json=None,
            prompt_tokens=0,
            completion_tokens=0,
            cost_estimate_usd=0.0,
            latency_ms=latency_ms,
            request_id=request_id,
            cached_system=False,
            backend_meta={
                "backend": "anthropic",
                "error": error,
                "error_msg": error_msg,
            },
        )

    def close(self) -> None:
        """Close the underlying httpx client. Safe to call multiple times."""
        self._client.close()

    def __enter__(self) -> AnthropicBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
