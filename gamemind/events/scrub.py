"""Amendment A10 secret redaction — regex-based filter on JSONL writers.

All JSONL payloads (events, brain_calls, errors, tracebacks) pass
through `scrub_secrets()` before write. This is the last-line-of-defense
guard against accidentally leaking an API key into a log file that
ships with a bug report.

Pattern: `sk-ant-[a-zA-Z0-9_-]{40,}` (Anthropic API key shape). Add
more patterns here if future backends introduce their own secret formats.
"""

from __future__ import annotations

import re
from typing import Any

# Anthropic API key: sk-ant-<40+ safe chars>
_ANTHROPIC_KEY_RE = re.compile(r"sk-ant-[a-zA-Z0-9_-]{40,}")

# Redacted placeholder
_REDACTED = "sk-ant-REDACTED"


def scrub_secrets(value: Any) -> Any:
    """Recursively scrub secret patterns from a JSON-compatible value.

    Handles:
      - str: regex-replace matching patterns
      - dict: recurse on values (keys are not scanned — unlikely to
              contain secrets by convention)
      - list / tuple: recurse on elements
      - other (int, float, bool, None): pass through unchanged

    Returns a new value of the same type. Does NOT mutate the input.
    """
    if isinstance(value, str):
        return _ANTHROPIC_KEY_RE.sub(_REDACTED, value)
    if isinstance(value, dict):
        return {k: scrub_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_secrets(v) for v in value]
    if isinstance(value, tuple):
        return tuple(scrub_secrets(v) for v in value)
    return value
