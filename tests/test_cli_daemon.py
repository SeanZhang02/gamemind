"""Unit tests for CLI daemon start/stop/status wiring.

We test _cmd_daemon_status and _cmd_daemon_stop in isolation with the
PID file monkey-patched to a tmp_path location. _cmd_daemon_start itself
binds uvicorn to a port — we can't easily test it in CI without a real
daemon, so we test the parser path and the PID file acquisition logic
separately.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gamemind import cli
from gamemind.daemon import pid as pid_module


@pytest.fixture
def tmp_pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect DEFAULT_PID_FILE to a tmp_path location for each test."""
    pid_file = tmp_path / "daemon.pid"
    monkeypatch.setattr(pid_module, "DEFAULT_PID_FILE", pid_file)
    return pid_file


def test_status_no_pid_file_returns_1(tmp_pid_file: Path, capsys: pytest.CaptureFixture) -> None:
    rc = cli._cmd_daemon_status("127.0.0.1", 8766)
    assert rc == 1
    captured = capsys.readouterr()
    assert "DOWN" in captured.out
    assert "no PID file" in captured.out


def test_status_stale_pid_file_returns_1(tmp_pid_file: Path, capsys: pytest.CaptureFixture) -> None:
    # Write a PID that's unlikely to exist
    tmp_pid_file.write_text(str(2**31 - 1))
    rc = cli._cmd_daemon_status("127.0.0.1", 8766)
    assert rc == 1
    captured = capsys.readouterr()
    assert "STALE PID" in captured.out


def test_stop_no_pid_file_returns_0(tmp_pid_file: Path, capsys: pytest.CaptureFixture) -> None:
    """No PID file → daemon is not running → stop is a no-op success."""
    rc = cli._cmd_daemon_stop("127.0.0.1", 8766)
    assert rc == 0
    captured = capsys.readouterr()
    assert "not running" in captured.out


def test_stop_stale_pid_file_cleans_up(tmp_pid_file: Path, capsys: pytest.CaptureFixture) -> None:
    """Stale PID file → stop removes it and reports success."""
    tmp_pid_file.write_text(str(2**31 - 1))
    assert tmp_pid_file.exists()
    rc = cli._cmd_daemon_stop("127.0.0.1", 8766)
    assert rc == 0
    captured = capsys.readouterr()
    assert "stale" in captured.out.lower()
    # PID file should be cleaned up
    assert not tmp_pid_file.exists()


def test_parser_accepts_daemon_subcommands() -> None:
    parser = cli._build_parser()

    for sub in ("start", "stop", "status"):
        args = parser.parse_args(["daemon", sub])
        assert args.command == "daemon"
        assert args.daemon_cmd == sub


def test_start_refuses_if_pid_file_holds_live_process(
    tmp_pid_file: Path, capsys: pytest.CaptureFixture
) -> None:
    """Happy path start-refusal: write our own PID, try to acquire → refused."""
    # Write current (live) PID → acquire should raise → _cmd_daemon_start
    # catches it and returns 1.
    tmp_pid_file.write_text(str(os.getpid()))
    rc = cli._cmd_daemon_start("127.0.0.1", 18766)  # bogus port — never reached
    assert rc == 1
    captured = capsys.readouterr()
    assert "refused" in captured.out
    assert "already running" in captured.out


def test_stop_on_current_process_uses_correct_signal(
    tmp_pid_file: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test the signal dispatch without actually killing ourselves.

    We write a PID, then mock os.kill to record the (pid, sig) call
    instead of sending it, so we can verify the right signal constant
    is used on each platform without process-suicide.
    """
    pid_written = 12345  # fake PID
    tmp_pid_file.write_text(str(pid_written))

    # Fake is_process_alive to return True (the test PID is "alive")
    monkeypatch.setattr(pid_module, "is_process_alive", lambda pid: pid == pid_written)

    kill_calls = []

    def fake_kill(pid: int, sig: int) -> None:
        kill_calls.append((pid, sig))

    monkeypatch.setattr(os, "kill", fake_kill)
    rc = cli._cmd_daemon_stop("127.0.0.1", 8766)
    assert rc == 0
    assert len(kill_calls) == 1
    assert kill_calls[0][0] == pid_written
    # Signal varies by platform but should be a positive int
    assert kill_calls[0][1] > 0
    captured = capsys.readouterr()
    assert f"pid={pid_written}" in captured.out


def test_stop_handles_os_kill_failure(
    tmp_pid_file: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.kill raises PermissionError, _cmd_daemon_stop should return 1."""
    pid_written = 54321
    tmp_pid_file.write_text(str(pid_written))
    monkeypatch.setattr(pid_module, "is_process_alive", lambda pid: pid == pid_written)

    def fake_kill_raises(pid: int, sig: int) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr(os, "kill", fake_kill_raises)
    rc = cli._cmd_daemon_stop("127.0.0.1", 8766)
    assert rc == 1
    captured = capsys.readouterr()
    assert "failed to signal" in captured.out
