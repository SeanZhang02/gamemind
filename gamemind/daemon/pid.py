"""PID file handling for `gamemind daemon start/stop/status`.

The PID file lives at `~/.gamemind/daemon.pid` by default. It's the
only source of truth for "is the daemon running." Callers that need
cross-platform process-alive checks use `is_process_alive(pid)` which
wraps OS-specific calls without a psutil dep.

Workflow:

    acquire_pid_file(path)      # on daemon start
    ... run uvicorn ...
    release_pid_file(path)      # on graceful shutdown (atexit)

    is_daemon_running(path)     # daemon stop / status
    read_pid(path)              # daemon stop (send signal)

A stale PID file (daemon crashed without releasing) is detected by
`is_process_alive(pid)` returning False; `acquire_pid_file()` will
then overwrite it. A LIVE PID file raises `RuntimeError` to prevent
two daemons on the same port.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

DEFAULT_PID_DIR = Path.home() / ".gamemind"
DEFAULT_PID_FILE = DEFAULT_PID_DIR / "daemon.pid"


def is_process_alive(pid: int) -> bool:
    """Return True iff a process with this PID exists and is alive.

    Cross-platform without psutil:
      - Unix: `os.kill(pid, 0)` raises ProcessLookupError if dead,
              PermissionError if alive-but-unreachable (still alive
              from our perspective).
      - Windows: ctypes OpenProcess + GetExitCodeProcess.

    PID 0 is treated as never-alive (invalid sentinel).
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _is_process_alive_windows(pid)
    return _is_process_alive_unix(pid)


def _is_process_alive_unix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still alive.
        return True


# Win32 constants (module-level so N806 doesn't flag them as function locals).
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _is_process_alive_windows(pid: int) -> bool:
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == _STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return False


def read_pid(pid_file: Path | None = None) -> int | None:
    """Read a PID from the file. Returns None on missing or malformed."""
    path = Path(pid_file) if pid_file is not None else DEFAULT_PID_FILE
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text)
    except (OSError, ValueError):
        return None


def is_daemon_running(pid_file: Path | None = None) -> bool:
    """True iff the PID file exists AND points to a live process."""
    pid = read_pid(pid_file)
    if pid is None:
        return False
    return is_process_alive(pid)


def acquire_pid_file(pid_file: Path | None = None) -> Path:
    """Write the current process PID to the file.

    Raises RuntimeError if the file already exists AND points to a
    live process — prevents two daemons on the same port. A STALE file
    (process dead) is overwritten silently.

    Returns the resolved path that was written.
    """
    path = Path(pid_file) if pid_file is not None else DEFAULT_PID_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing_pid = read_pid(path)
        if existing_pid is not None and is_process_alive(existing_pid):
            raise RuntimeError(
                f"gamemind daemon already running (pid={existing_pid}) "
                f"per {path}; run `gamemind daemon stop` first"
            )
        # Stale — remove so we can overwrite cleanly.
        with contextlib.suppress(OSError):
            path.unlink()
    path.write_text(str(os.getpid()), encoding="utf-8")
    return path


def release_pid_file(pid_file: Path | None = None) -> None:
    """Remove the PID file. Safe to call if it doesn't exist.

    Only removes the file if it contains OUR PID — prevents accidentally
    clobbering a replacement daemon's PID file if shutdown races.
    """
    path = Path(pid_file) if pid_file is not None else DEFAULT_PID_FILE
    if not path.exists():
        return
    current = read_pid(path)
    if current is not None and current == os.getpid():
        with contextlib.suppress(OSError):
            path.unlink()
