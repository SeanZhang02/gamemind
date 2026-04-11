"""Named session outcomes per §1.4 and §1.7 of docs/final-design.md.

Every session ends with exactly one `Outcome`. The outcome is what the
events.jsonl terminal event (`session_complete` / `session_aborted_*`)
carries in `payload.outcome`. Downstream analyzers (replay harness,
metrics dashboards, v2-T2 skill compounding metric) filter on this
field, so the set is closed — additions require a PR to this file
AND a corresponding `session_aborted_*` entry in
`gamemind/events/envelope.py::_KNOWN_EVENT_TYPES`.
"""

from __future__ import annotations

from typing import Literal

# Terminal outcomes — the full closed set.
Outcome = Literal[
    # Success paths
    "success",
    # Amendment A6 / §1.7 backend absence
    "perception_unavailable",
    "brain_unavailable",
    "brain_rate_limited",
    # §1.4 W triggers that terminate
    "runaway",  # 30-call kill switch
    # §1.6 disagreement recovery tier 5
    "perception_disagreement_unresolvable",
    # Adapter abort conditions (§1.4 W3)
    "aborted",
    # Layer 4 action errors
    "input_target_lost",
    # Layer 0 capture errors
    "capture_unavailable",
    # Top-level catch-all
    "unhandled_exception",
    # User-initiated stop via CLI / HTTP
    "user_stopped",
]

_SUCCESS_OUTCOMES: frozenset[str] = frozenset({"success"})

_ALL_OUTCOMES: frozenset[str] = frozenset(
    {
        "success",
        "perception_unavailable",
        "brain_unavailable",
        "brain_rate_limited",
        "runaway",
        "perception_disagreement_unresolvable",
        "aborted",
        "input_target_lost",
        "capture_unavailable",
        "unhandled_exception",
        "user_stopped",
    }
)


def is_terminal_outcome(value: str) -> bool:
    """True iff `value` is a recognized terminal outcome."""
    return value in _ALL_OUTCOMES


def is_success_outcome(value: str) -> bool:
    """True iff `value` is a success (not an error/abort) outcome."""
    return value in _SUCCESS_OUTCOMES


__all__ = ["Outcome", "is_success_outcome", "is_terminal_outcome"]
