"""GameMind — universal game AI agent framework.

Declarative over two axes: games (via YAML adapter) and models (via
OpenAI-compat LLMBackend). Same daemon binary, swap data not code.

See `docs/final-design.md` for the authoritative architecture and
`docs/final-design.md` §10 for the autoplan review that gated Phase C.
"""

from __future__ import annotations

__version__ = "0.1.0"
