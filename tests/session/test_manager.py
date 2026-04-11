"""Unit tests for SessionManager — uses tmp_path for filesystem isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamemind.session import Outcome, SessionManager, is_terminal_outcome
from gamemind.session.manager import (
    NoActiveSessionError,
    SessionAlreadyRunningError,
)


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager()


def test_initial_state_is_idle(manager: SessionManager) -> None:
    info = manager.snapshot()
    assert info.status == "idle"
    assert info.session_id is None
    assert info.outcome is None
    assert info.events_path is None
    assert manager.is_running() is False


def test_start_transitions_to_running(manager: SessionManager, tmp_path: Path) -> None:
    info = manager.start(
        adapter_path=Path("adapters/test.yaml"),
        task_description="test task",
        runs_root=tmp_path / "runs",
    )
    assert info.status == "running"
    assert info.session_id is not None
    assert len(info.session_id) == 12  # UUID4 hex truncated
    assert info.events_path is not None
    assert info.task_description == "test task"
    assert manager.is_running() is True


def test_start_twice_raises(manager: SessionManager, tmp_path: Path) -> None:
    manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    with pytest.raises(SessionAlreadyRunningError):
        manager.start(
            adapter_path=Path("b.yaml"),
            task_description="u",
            runs_root=tmp_path / "runs",
        )


def test_start_creates_events_file_with_session_start(
    manager: SessionManager, tmp_path: Path
) -> None:
    info = manager.start(
        adapter_path=Path("adapters/mc.yaml"),
        task_description="chop logs",
        runs_root=tmp_path / "runs",
    )
    # Terminate to flush the writer
    manager.transition_to_terminal(outcome="success")

    events_path = Path(info.events_path)
    assert events_path.exists()
    lines = events_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) >= 2  # session_start + session_complete
    first_event = json.loads(lines[0])
    assert first_event["producer"] == "session"
    assert first_event["event_type"] == "session_start"
    # Path separator varies by platform — check via endswith on the normalized path
    assert first_event["payload"]["adapter_path"].replace("\\", "/").endswith("adapters/mc.yaml")
    assert first_event["payload"]["task"] == "chop logs"


def test_transition_to_success(manager: SessionManager, tmp_path: Path) -> None:
    manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    info = manager.transition_to_terminal(outcome="success")
    assert info.status == "terminal"
    assert info.outcome == "success"
    assert manager.is_running() is False


def test_transition_emits_session_complete(manager: SessionManager, tmp_path: Path) -> None:
    info = manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    manager.transition_to_terminal(outcome="success")

    lines = Path(info.events_path).read_text(encoding="utf-8").strip().split("\n")
    last_event = json.loads(lines[-1])
    assert last_event["event_type"] == "session_complete"
    assert last_event["payload"]["outcome"] == "success"


def test_transition_runaway_emits_aborted_runaway(manager: SessionManager, tmp_path: Path) -> None:
    info = manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    manager.transition_to_terminal(outcome="runaway", brain_call_count=31)
    lines = Path(info.events_path).read_text(encoding="utf-8").strip().split("\n")
    last_event = json.loads(lines[-1])
    assert last_event["event_type"] == "session_aborted_runaway"
    assert last_event["payload"]["brain_call_count"] == 31


def test_transition_brain_unavailable(manager: SessionManager, tmp_path: Path) -> None:
    info = manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    manager.transition_to_terminal(outcome="brain_unavailable")
    lines = Path(info.events_path).read_text(encoding="utf-8").strip().split("\n")
    last_event = json.loads(lines[-1])
    assert last_event["event_type"] == "session_aborted_brain_unavailable"


def test_transition_without_running_raises(manager: SessionManager) -> None:
    with pytest.raises(NoActiveSessionError):
        manager.transition_to_terminal(outcome="success")


def test_transition_unknown_outcome_raises(manager: SessionManager, tmp_path: Path) -> None:
    manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    with pytest.raises(ValueError, match="unknown outcome"):
        manager.transition_to_terminal(outcome="not_a_real_outcome")  # type: ignore[arg-type]


def test_reset_from_terminal(manager: SessionManager, tmp_path: Path) -> None:
    manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    manager.transition_to_terminal(outcome="success")
    assert manager.snapshot().status == "terminal"
    manager.reset()
    assert manager.snapshot().status == "idle"
    assert manager.snapshot().session_id is None


def test_reset_while_running_raises(manager: SessionManager, tmp_path: Path) -> None:
    manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t",
        runs_root=tmp_path / "runs",
    )
    with pytest.raises(SessionAlreadyRunningError):
        manager.reset()


def test_start_after_reset_is_ok(manager: SessionManager, tmp_path: Path) -> None:
    manager.start(
        adapter_path=Path("a.yaml"),
        task_description="t1",
        runs_root=tmp_path / "runs",
    )
    manager.transition_to_terminal(outcome="success")
    manager.reset()
    info = manager.start(
        adapter_path=Path("b.yaml"),
        task_description="t2",
        runs_root=tmp_path / "runs",
    )
    assert info.status == "running"
    assert info.task_description == "t2"
    assert info.session_id is not None


def test_new_session_ids_are_unique(manager: SessionManager, tmp_path: Path) -> None:
    ids: set[str] = set()
    for i in range(10):
        manager.start(
            adapter_path=Path(f"a{i}.yaml"),
            task_description=f"t{i}",
            runs_root=tmp_path / "runs",
        )
        info = manager.snapshot()
        assert info.session_id is not None
        ids.add(info.session_id)
        manager.transition_to_terminal(outcome="success")
        manager.reset()
    assert len(ids) == 10  # all unique


def test_is_terminal_outcome_exports() -> None:
    assert is_terminal_outcome("success") is True
    assert is_terminal_outcome("runaway") is True
    assert is_terminal_outcome("perception_unavailable") is True
    assert is_terminal_outcome("not_real") is False


def test_outcome_type_alias_accessible() -> None:
    """The Outcome Literal should be importable from gamemind.session."""
    # Just check the import path works; the literal itself has no runtime shape
    assert Outcome is not None
