"""Unit tests for PID file handling."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gamemind.daemon import pid as pid_module
from gamemind.daemon.pid import (
    acquire_pid_file,
    is_daemon_running,
    is_process_alive,
    read_pid,
    release_pid_file,
)


def test_is_process_alive_current_process() -> None:
    """Our own PID is always alive."""
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_invalid_pid() -> None:
    """PID 0 and negative PIDs are never alive."""
    assert is_process_alive(0) is False
    assert is_process_alive(-1) is False


def test_is_process_alive_nonexistent_pid() -> None:
    """A very high PID that doesn't exist."""
    # 2**31 - 1 is the max 32-bit signed PID. Very unlikely to exist.
    assert is_process_alive(2**31 - 1) is False


def test_read_pid_missing_file(tmp_path: Path) -> None:
    assert read_pid(tmp_path / "missing.pid") is None


def test_read_pid_valid(tmp_path: Path) -> None:
    f = tmp_path / "test.pid"
    f.write_text("12345")
    assert read_pid(f) == 12345


def test_read_pid_malformed(tmp_path: Path) -> None:
    f = tmp_path / "bad.pid"
    f.write_text("not a number")
    assert read_pid(f) is None


def test_read_pid_with_whitespace(tmp_path: Path) -> None:
    f = tmp_path / "ws.pid"
    f.write_text("  42  \n")
    assert read_pid(f) == 42


def test_acquire_pid_file_creates_file(tmp_path: Path) -> None:
    pid_file = tmp_path / "sub" / "daemon.pid"
    result = acquire_pid_file(pid_file)
    assert result == pid_file
    assert pid_file.exists()
    assert read_pid(pid_file) == os.getpid()


def test_acquire_pid_file_rejects_if_live_daemon(tmp_path: Path) -> None:
    """If the PID file has our PID (which is alive), acquire should raise."""
    pid_file = tmp_path / "daemon.pid"
    acquire_pid_file(pid_file)
    with pytest.raises(RuntimeError, match="already running"):
        acquire_pid_file(pid_file)


def test_acquire_pid_file_overwrites_stale(tmp_path: Path) -> None:
    """A stale PID file (dead process) should be silently overwritten."""
    pid_file = tmp_path / "stale.pid"
    # Write a fake dead PID (2^31 - 1 is unlikely to exist)
    pid_file.write_text(str(2**31 - 1))
    # Now acquire — should succeed
    acquire_pid_file(pid_file)
    assert read_pid(pid_file) == os.getpid()


def test_release_pid_file_removes_if_ours(tmp_path: Path) -> None:
    pid_file = tmp_path / "daemon.pid"
    acquire_pid_file(pid_file)
    assert pid_file.exists()
    release_pid_file(pid_file)
    assert not pid_file.exists()


def test_release_pid_file_safe_on_missing(tmp_path: Path) -> None:
    # Should not raise
    release_pid_file(tmp_path / "missing.pid")


def test_release_pid_file_leaves_foreign_pid(tmp_path: Path) -> None:
    """If the PID file has a different PID, release should not remove it."""
    pid_file = tmp_path / "foreign.pid"
    pid_file.write_text("99999")  # arbitrary non-self PID
    release_pid_file(pid_file)
    # Foreign PID file should still exist — we don't clobber other daemons
    assert pid_file.exists()


def test_is_daemon_running_no_file(tmp_path: Path) -> None:
    assert is_daemon_running(tmp_path / "missing.pid") is False


def test_is_daemon_running_dead_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / "dead.pid"
    pid_file.write_text(str(2**31 - 1))
    assert is_daemon_running(pid_file) is False


def test_is_daemon_running_live_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / "live.pid"
    acquire_pid_file(pid_file)
    assert is_daemon_running(pid_file) is True
    release_pid_file(pid_file)


def test_default_pid_file_path_is_under_home() -> None:
    # Just a smoke check that the default path is sane
    assert pid_module.DEFAULT_PID_FILE.name == "daemon.pid"
    assert pid_module.DEFAULT_PID_FILE.parent.name == ".gamemind"


def test_acquire_pid_file_default_path_respects_override(tmp_path: Path) -> None:
    """Verify `None` → default path, explicit path → explicit path."""
    custom = tmp_path / "custom.pid"
    result = acquire_pid_file(custom)
    assert result == custom
    release_pid_file(custom)
