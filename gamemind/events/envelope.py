"""Amendment A2 event envelope — schema v1.

Frozen contract per docs/final-design.md §1.4.A. All producers (capture,
perception, layer2, brain, verify, action, replay, session) use this
envelope; the `event_type` literal set is enumerated below and CI-checked
at some future point.

Breaking changes to the envelope shape bump `CURRENT_SCHEMA_VERSION` and
require a migration in `gamemind/events/migrations/v{from}_to_v{to}.py`
(not shipped until a breaking change is needed).
"""

from __future__ import annotations

import time
from datetime import datetime, UTC
from typing import Any, Literal, TypedDict

CURRENT_SCHEMA_VERSION: int = 1

Producer = Literal[
    "capture",
    "perception",
    "layer2",
    "brain",
    "verify",
    "action",
    "replay",
    "session",
]

# Enumerated event_type values per §1.4.A. Additions require a
# docs/events-schema.md entry.
EventType = Literal[
    # capture
    "capture_ok",
    "capture_black_frame",
    "capture_backend_swap",
    # perception
    "perception_tick",
    "perception_stale_dropped",
    "perception_json_error",
    "perception_think_leak",
    # layer2
    "stuck_detected",
    "abort_condition_fired",
    # brain
    "wake_w1",
    "wake_w2",
    "wake_w3",
    "wake_w4",
    "wake_w5",
    "brain_response_ok",
    "brain_response_error",
    "brain_rate_limited",
    # verify
    "predicate_fired",
    "perception_disagreement",
    "self_correction",
    "layer_1_majority_wins",
    "arbiter_resolution",
    # action
    "action_executed",
    "action_dropped_focus",
    "action_dropped_target_lost",
    "action_repetition_guard_fired",
    # replay
    "replay_load",
    "replay_step_ok",
    "replay_diff",
    # session
    "session_start",
    "session_complete",
    "session_aborted_runaway",
    "session_aborted_perception_unavailable",
    "session_aborted_brain_unavailable",
    "session_aborted_unhandled_exception",
]

# Producer → allowed event_type prefixes, for a cheap runtime sanity
# check. Not a security boundary (producers are in-process), just
# helps catch "perception emits wake_w1" typo bugs at test time.
_PRODUCER_PREFIXES: dict[Producer, tuple[str, ...]] = {
    "capture": ("capture_",),
    "perception": ("perception_",),
    "layer2": ("stuck_", "abort_"),
    "brain": ("wake_", "brain_"),
    "verify": (
        "predicate_",
        "perception_disagreement",
        "self_correction",
        "layer_1_majority_wins",
        "arbiter_resolution",
    ),
    "action": ("action_",),
    "replay": ("replay_",),
    "session": ("session_",),
}


class Envelope(TypedDict):
    """Shape of a single JSONL event line. Matches docs/final-design.md §1.4.A."""

    schema_version: int
    session_id: str
    ts_monotonic_ns: int
    ts_wall: str
    frame_id: str | None
    producer: Producer
    event_type: str
    payload: dict[str, Any]


def make_envelope(
    *,
    session_id: str,
    producer: Producer,
    event_type: EventType | str,
    payload: dict[str, Any] | None = None,
    frame_id: str | None = None,
) -> Envelope:
    """Construct an envelope with correct schema_version and timestamps.

    `event_type` accepts any str (not just the enumerated literal) so
    tests can inject unknown values; runtime producer-prefix check still
    runs for enumerated types.
    """
    if event_type in _known_event_types():
        expected_prefixes = _PRODUCER_PREFIXES.get(producer, ())
        if expected_prefixes and not any(
            event_type.startswith(pfx) or event_type == pfx.rstrip("_") for pfx in expected_prefixes
        ):
            # Not a hard failure — just a mismatch warning embedded in
            # the payload for debuggability. The CI events-schema lint
            # catches persistent violations.
            payload = dict(payload or {})
            payload.setdefault(
                "_envelope_warning",
                f"event_type={event_type!r} unusual for producer={producer!r}",
            )

    return Envelope(
        schema_version=CURRENT_SCHEMA_VERSION,
        session_id=session_id,
        ts_monotonic_ns=time.monotonic_ns(),
        ts_wall=datetime.now(tz=UTC).isoformat(),
        frame_id=frame_id,
        producer=producer,
        event_type=event_type,
        payload=payload or {},
    )


def _known_event_types() -> frozenset[str]:
    """Return all known event_type literals as a frozenset for fast lookup."""
    return _KNOWN_EVENT_TYPES


_KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "capture_ok",
        "capture_black_frame",
        "capture_backend_swap",
        "perception_tick",
        "perception_stale_dropped",
        "perception_json_error",
        "perception_think_leak",
        "stuck_detected",
        "abort_condition_fired",
        "wake_w1",
        "wake_w2",
        "wake_w3",
        "wake_w4",
        "wake_w5",
        "brain_response_ok",
        "brain_response_error",
        "brain_rate_limited",
        "predicate_fired",
        "perception_disagreement",
        "self_correction",
        "layer_1_majority_wins",
        "arbiter_resolution",
        "action_executed",
        "action_dropped_focus",
        "action_dropped_target_lost",
        "action_repetition_guard_fired",
        "replay_load",
        "replay_step_ok",
        "replay_diff",
        "session_start",
        "session_complete",
        "session_aborted_runaway",
        "session_aborted_perception_unavailable",
        "session_aborted_brain_unavailable",
        "session_aborted_unhandled_exception",
    }
)
