"""Layer 3 — Brain.

OpenAI-compat `LLMBackend` Protocol for sparse cloud brain wakes and
continuous local perception. Implementations:

- `gamemind.brain.backend.LLMBackend` Protocol + `LLMResponse` dataclass
  (frozen per Amendment A12, §OQ-3 addendum).
- `gamemind.perception.ollama_backend.OllamaBackend` — first implementor,
  used by Layer 1 continuous perception.
- `gamemind.brain.anthropic_backend.AnthropicBackend` — future, for Layer 3
  sparse wakes with prompt caching.
- `gamemind.brain.gemini_backend.GeminiBackend` — future, W4 vision-critic
  escalation only (not a brain absence fallback per Amendment A6).
"""

from __future__ import annotations

from gamemind.brain.anthropic_backend import AnthropicBackend
from gamemind.brain.backend import LLMBackend, LLMResponse

__all__ = ["AnthropicBackend", "LLMBackend", "LLMResponse"]
