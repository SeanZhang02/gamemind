"""Layer 6 — Game Adapter.

Declarative YAML adapter schema per `docs/final-design.md §2 OQ-4` +
§3 Rule 2. Game knowledge lives in `adapters/*.yaml` files; no per-game
Python anywhere. The schema is pydantic-strict (unknown keys rejected)
per Amendment A8, and path-traversal-hardened per Amendment A9.

The v1 schema covers the fields Phase C Step 3 needs to run a first
end-to-end chop_logs on Minecraft. Subsequent schema extensions bump
`schema_version` so future adapter authors get a clear migration path
(Amendment A1 schema versioning).

Entry points:
  load(path) -> Adapter — validate + construct
  validate(path) -> list[str] — lint without constructing
"""

from __future__ import annotations

from gamemind.adapter.loader import load, validate
from gamemind.adapter.schema import (
    CURRENT_SCHEMA_VERSION,
    Adapter,
    AbortCondition,
    GoalGrammar,
    Predicate,
    SuccessCheck,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "AbortCondition",
    "Adapter",
    "GoalGrammar",
    "Predicate",
    "SuccessCheck",
    "load",
    "validate",
]
