"""Unit tests for Amendment A2 event envelope."""

from __future__ import annotations

from gamemind.events.envelope import (
    CURRENT_SCHEMA_VERSION,
    make_envelope,
)


def test_schema_version_is_1() -> None:
    assert CURRENT_SCHEMA_VERSION == 1


def test_make_envelope_minimal() -> None:
    env = make_envelope(
        session_id="abc123",
        producer="perception",
        event_type="perception_tick",
    )
    assert env["schema_version"] == 1
    assert env["session_id"] == "abc123"
    assert env["producer"] == "perception"
    assert env["event_type"] == "perception_tick"
    assert env["payload"] == {}
    assert env["frame_id"] is None
    assert env["ts_monotonic_ns"] > 0
    assert "T" in env["ts_wall"]  # ISO 8601 marker
    assert env["ts_wall"].endswith("+00:00")  # UTC timezone


def test_make_envelope_with_payload_and_frame_id() -> None:
    env = make_envelope(
        session_id="abc",
        producer="brain",
        event_type="wake_w1",
        payload={"plan": "approach tree", "tokens": 128},
        frame_id="frame-042",
    )
    assert env["frame_id"] == "frame-042"
    assert env["payload"]["plan"] == "approach tree"


def test_make_envelope_warns_on_producer_type_mismatch() -> None:
    """perception producer emitting wake_w1 should get a warning in payload."""
    env = make_envelope(
        session_id="abc",
        producer="perception",
        event_type="wake_w1",
    )
    assert "_envelope_warning" in env["payload"]


def test_make_envelope_no_warning_on_correct_producer() -> None:
    env = make_envelope(
        session_id="abc",
        producer="brain",
        event_type="wake_w1",
    )
    assert "_envelope_warning" not in env["payload"]


def test_make_envelope_unknown_event_type_no_warning() -> None:
    """Unknown event_type passes through without producer-prefix check."""
    env = make_envelope(
        session_id="abc",
        producer="perception",
        event_type="custom_unknown_event",
    )
    # No warning since the event_type isn't in the known set
    assert "_envelope_warning" not in env["payload"]


def test_all_producers_covered() -> None:
    """Every literal Producer value should be usable."""
    for producer in [
        "capture",
        "perception",
        "layer2",
        "brain",
        "verify",
        "action",
        "replay",
        "session",
    ]:
        env = make_envelope(
            session_id="abc",
            producer=producer,  # type: ignore[arg-type]
            event_type="test",
        )
        assert env["producer"] == producer


def test_monotonic_ns_increases() -> None:
    """Successive envelopes should have strictly-monotonic ts_monotonic_ns."""
    env1 = make_envelope(session_id="a", producer="perception", event_type="perception_tick")
    env2 = make_envelope(session_id="a", producer="perception", event_type="perception_tick")
    assert env2["ts_monotonic_ns"] > env1["ts_monotonic_ns"]
