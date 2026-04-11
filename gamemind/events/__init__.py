"""Events writer — Amendment A2 event envelope + Amendment A10 secrets scrub.

All writers to `runs/<session>/events.jsonl` and
`runs/<session>/brain_calls.jsonl` route through this module so the
schema_version, secret redaction, and thread-pool batching are enforced
in exactly one place.

Entry points:
  EventWriter — append-only writer with batched flushing
  make_envelope() — construct a schema v1 envelope from payload
  scrub_secrets() — regex redaction for Anthropic API keys
"""

from __future__ import annotations

from gamemind.events.envelope import CURRENT_SCHEMA_VERSION, Producer, make_envelope
from gamemind.events.scrub import scrub_secrets
from gamemind.events.writer import EventWriter

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EventWriter",
    "Producer",
    "make_envelope",
    "scrub_secrets",
]
