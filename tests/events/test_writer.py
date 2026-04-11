"""Unit tests for EventWriter — tmp_path for filesystem isolation."""

from __future__ import annotations

import json
from pathlib import Path


from gamemind.events.envelope import make_envelope
from gamemind.events.writer import EventWriter


FAKE_KEY = "sk-ant-" + "x" * 50
REDACTED = "sk-ant-REDACTED"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    return [json.loads(line) for line in lines if line]


def test_writer_creates_files(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-1"
    with EventWriter(session_dir) as w:
        pass
    assert w.events_path == session_dir / "events.jsonl"
    assert w.brain_calls_path == session_dir / "brain_calls.jsonl"
    assert w.events_path.exists()
    assert w.brain_calls_path.exists()


def test_writer_writes_events_jsonl(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-2"
    with EventWriter(session_dir) as w:
        env = make_envelope(
            session_id="sess-2",
            producer="perception",
            event_type="perception_tick",
            payload={"frame_id": "f001"},
        )
        assert w.write(env) is True
    events = _read_jsonl(w.events_path)
    assert len(events) == 1
    assert events[0]["event_type"] == "perception_tick"
    assert events[0]["payload"] == {"frame_id": "f001"}


def test_writer_routes_brain_events_to_brain_calls_jsonl(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-3"
    with EventWriter(session_dir) as w:
        w.write(
            make_envelope(
                session_id="sess-3",
                producer="brain",
                event_type="wake_w1",
                payload={"plan": "x"},
            )
        )
        w.write(
            make_envelope(
                session_id="sess-3",
                producer="perception",
                event_type="perception_tick",
            )
        )
        w.write(
            make_envelope(
                session_id="sess-3",
                producer="brain",
                event_type="brain_response_ok",
            )
        )
    events = _read_jsonl(w.events_path)
    brain_calls = _read_jsonl(w.brain_calls_path)
    assert len(events) == 3
    assert len(brain_calls) == 2
    assert all(ev["producer"] == "brain" for ev in brain_calls)


def test_writer_scrubs_secrets(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-4"
    with EventWriter(session_dir) as w:
        w.write(
            make_envelope(
                session_id="sess-4",
                producer="brain",
                event_type="brain_response_error",
                payload={"error_msg": f"401 Unauthorized, key={FAKE_KEY}"},
            )
        )
    events = _read_jsonl(w.events_path)
    assert len(events) == 1
    assert FAKE_KEY not in events[0]["payload"]["error_msg"]
    assert REDACTED in events[0]["payload"]["error_msg"]


def test_writer_drops_on_full_queue(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-5"
    # Tiny queue so we can overflow it
    w = EventWriter(session_dir, queue_max=2)
    # Don't start the drain thread — fills the queue
    accepts = []
    for i in range(5):
        env = make_envelope(
            session_id="sess-5",
            producer="perception",
            event_type="perception_tick",
            payload={"i": i},
        )
        accepts.append(w.write(env))
    # First 2 fit, remaining 3 drop
    assert accepts == [True, True, False, False, False]
    assert w.drop_count == 3
    # Now start + close to drain what IS in the queue
    w.start()
    w.close()
    events = _read_jsonl(w.events_path)
    assert len(events) == 2


def test_writer_write_after_close_returns_false(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-6"
    w = EventWriter(session_dir)
    w.start()
    w.write(
        make_envelope(
            session_id="sess-6",
            producer="perception",
            event_type="perception_tick",
        )
    )
    w.close()
    result = w.write(
        make_envelope(
            session_id="sess-6",
            producer="perception",
            event_type="perception_tick",
        )
    )
    assert result is False


def test_writer_flushes_on_terminal_event(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-7"
    w = EventWriter(session_dir)
    w.start()
    w.write(
        make_envelope(
            session_id="sess-7",
            producer="session",
            event_type="session_start",
        )
    )
    w.write(
        make_envelope(
            session_id="sess-7",
            producer="session",
            event_type="session_complete",
            payload={"outcome": "success"},
        )
    )
    w.close()
    events = _read_jsonl(w.events_path)
    assert len(events) == 2
    assert events[0]["event_type"] == "session_start"
    assert events[1]["event_type"] == "session_complete"


def test_writer_write_count(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-8"
    with EventWriter(session_dir) as w:
        for i in range(10):
            w.write(
                make_envelope(
                    session_id="sess-8",
                    producer="perception",
                    event_type="perception_tick",
                    payload={"i": i},
                )
            )
    assert w.write_count == 10
    assert w.drop_count == 0


def test_writer_unicode_payload_roundtrips(tmp_path: Path) -> None:
    session_dir = tmp_path / "sess-9"
    with EventWriter(session_dir) as w:
        w.write(
            make_envelope(
                session_id="sess-9",
                producer="brain",
                event_type="wake_w1",
                payload={"text": "测试 unicode ✓ 🎮"},
            )
        )
    events = _read_jsonl(w.events_path)
    assert events[0]["payload"]["text"] == "测试 unicode ✓ 🎮"
