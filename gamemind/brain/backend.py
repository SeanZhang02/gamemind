"""LLMBackend Protocol + LLMResponse dataclass (Amendment A12).

Frozen per §OQ-3 addendum of `docs/final-design.md`. Both Layer 1
(perception, continuous, Ollama) and Layer 3 (brain, sparse, Anthropic
+ optional Gemini W4 escalation) satisfy this Protocol.

The consumer-side contract is deliberately minimal. Backend-specific
metadata (Ollama's `total_duration_ns`, Anthropic's prompt caching
statistics, Gemini's safety metadata) go into `backend_meta: dict` on
`LLMResponse` — an escape hatch for backend-internal fields the generic
caller doesn't need but that metrics / debugging might want.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResponse:
    """Backend-agnostic response envelope.

    Fields:
      text: raw string response (never None; empty string on parse failure).
      parsed_json: parsed dict if backend returned structured JSON,
                   None otherwise. Callers should NOT re-parse `text`.
      prompt_tokens: input tokens counted by backend (0 if unknown).
      completion_tokens: output tokens counted by backend (0 if unknown).
      cost_estimate_usd: USD cost computed per-backend from token counts
                         and the backend's cost table. Local models
                         (Ollama) report 0.0.
      latency_ms: wall-clock milliseconds from call start to response
                  return (not inference-only — includes network + JSON
                  decode).
      request_id: correlates to `runs/<session>/brain_calls.jsonl` per
                  Amendment A2. Caller supplies; backend echoes.
      cached_system: True iff the backend used a cache hit on the system
                     prompt (Anthropic prompt caching). Local backends
                     report False.
      backend_meta: escape hatch for backend-specific metadata. Not
                    part of the Protocol contract; callers MUST NOT
                    depend on specific keys.
    """

    text: str
    parsed_json: dict[str, Any] | None
    prompt_tokens: int
    completion_tokens: int
    cost_estimate_usd: float
    latency_ms: float
    request_id: str
    cached_system: bool
    backend_meta: dict[str, Any] = field(default_factory=dict)


class LLMBackend(Protocol):
    """OpenAI-compat chat-completion interface for Layer 1 and Layer 3.

    Implementations MUST:
      - Honor `temperature` and `max_tokens` exactly (no silent override)
      - Echo `request_id` on the response unchanged
      - Return `LLMResponse.text = ""` and `parsed_json = None` on backend
        error, never raise (raise only on caller-error like bad arguments)
      - Emit `brain_calls.jsonl` events via the shared events writer iff
        the caller passes `emit_event=True` (default True for Layer 3,
        False for Layer 1 per-frame perception to avoid log explosion)

    Implementations MAY:
      - Use `cache_system` to enable prompt caching (Anthropic) or
        ignore it (Ollama)
      - Populate `backend_meta` with internal diagnostics

    Error taxonomy per Amendment A6 — raise `gamemind.errors.*` for
    backend absence / network failure / auth failure so callers can
    route recovery policies.
    """

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
