"""gamemind CLI — argparse entry point.

Wired as `gamemind = "gamemind.cli:main"` in pyproject.toml.

Phase C Step 1 scaffolds the subcommand surface; each subcommand is a
stub that prints its scope and exits 0. Subsequent commits wire each
subcommand to real daemon / doctor / run logic.

Subcommand map:
  gamemind daemon start|stop|status
  gamemind doctor --capture | --input | --live-perception | --all
  gamemind run --adapter <path> --task <desc>
  gamemind adapter validate <path>     (Step 3)
  gamemind replay <run_id> --only-brain --frame <n>   (Step 3 minimal, Amendment A5)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamemind",
        description="Universal game AI agent framework (see docs/final-design.md)",
    )
    parser.add_argument("--version", action="version", version="gamemind 0.1.0")
    sub = parser.add_subparsers(dest="command", required=False)

    # daemon
    daemon = sub.add_parser("daemon", help="Start, stop, or check the FastAPI daemon")
    daemon_sub = daemon.add_subparsers(dest="daemon_cmd", required=True)
    daemon_sub.add_parser("start", help="Start the FastAPI daemon on 127.0.0.1:8766")
    daemon_sub.add_parser("stop", help="Stop the running daemon")
    daemon_sub.add_parser("status", help="Check daemon status")

    # doctor
    doctor = sub.add_parser("doctor", help="Diagnose capture / input / perception stack")
    doctor.add_argument("--capture", action="store_true", help="Run capture doctor")
    doctor.add_argument("--input", action="store_true", help="Run input doctor (key loopback)")
    doctor.add_argument(
        "--live-perception",
        action="store_true",
        help="Run 60s live 2-3 Hz perception spike (Amendment A1 freshness gate)",
    )
    doctor.add_argument(
        "--all",
        action="store_true",
        help="Run all doctor sub-checks with scripted remediation (DX-SUB-2)",
    )
    doctor.add_argument("--window-title", default=None, help="Filter HWND by window title")

    # run
    run = sub.add_parser("run", help="Run an agent session with an adapter")
    run.add_argument("--adapter", type=Path, required=True, help="Path to adapter YAML")
    run.add_argument("--task", type=str, required=True, help="Task description in natural language")

    return parser


def _cmd_daemon_start(daemon_host: str, daemon_port: int) -> int:
    """Acquire PID file, bind uvicorn, release PID on shutdown.

    Per Amendment A3: bind ONLY to 127.0.0.1. Never 0.0.0.0. The host
    arg exists only to support tests that want a non-default loopback
    port — all production starts hit 127.0.0.1:8766.
    """
    # Late imports so `gamemind --version` doesn't pull in uvicorn/anthropic.
    import atexit  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    from gamemind.daemon.main import app  # noqa: PLC0415
    from gamemind.daemon.pid import acquire_pid_file, release_pid_file  # noqa: PLC0415

    try:
        pid_file = acquire_pid_file()
    except RuntimeError as exc:
        print(f"[gamemind daemon start] refused: {exc}")
        return 1

    print(f"[gamemind daemon start] bound to http://{daemon_host}:{daemon_port}")
    print(f"  pid={os.getpid()} pid_file={pid_file}")
    print("  (Ctrl+C to stop; /healthz is unauthenticated, /v1/* requires bearer token)")

    # Register cleanup BEFORE uvicorn.run so a Ctrl+C or uvicorn shutdown
    # still releases the PID file.
    atexit.register(release_pid_file)

    try:
        uvicorn.run(app, host=daemon_host, port=daemon_port, log_level="info")
    finally:
        # Belt-and-suspenders — atexit also runs, but explicit release
        # here covers the non-exit-path case.
        release_pid_file()

    return 0


def _cmd_daemon_status(daemon_host: str, daemon_port: int) -> int:
    """Check daemon health via /healthz."""
    import httpx  # noqa: PLC0415

    from gamemind.daemon.pid import is_daemon_running, read_pid  # noqa: PLC0415

    pid = read_pid()
    running = is_daemon_running()
    if pid is None:
        print("[gamemind daemon status] DOWN (no PID file)")
        return 1
    if not running:
        print(f"[gamemind daemon status] STALE PID {pid} (process not alive)")
        return 1

    # PID file says running — verify via /healthz for the full truth.
    try:
        response = httpx.get(f"http://{daemon_host}:{daemon_port}/healthz", timeout=2.0)
        response.raise_for_status()
        print(f"[gamemind daemon status] UP pid={pid}")
        print(f"  {response.json()}")
        return 0
    except httpx.HTTPError as exc:
        print(f"[gamemind daemon status] PID {pid} alive but /healthz unreachable: {exc}")
        return 1


def _cmd_daemon_stop(daemon_host: str, daemon_port: int) -> int:  # noqa: ARG001
    """Signal the running daemon to shut down.

    Step 1 iter-8 scope: on Unix, sends SIGTERM to the PID in the PID file.
    On Windows, uses CTRL_BREAK_EVENT via os.kill. If neither works (rare),
    asks the user to Ctrl+C the foreground process. Real graceful shutdown
    via a POST /v1/daemon/stop endpoint is a later iter.
    """
    import contextlib  # noqa: PLC0415
    import signal  # noqa: PLC0415

    from gamemind.daemon.pid import (  # noqa: PLC0415
        DEFAULT_PID_FILE,
        is_process_alive,
        read_pid,
    )

    pid = read_pid()
    if pid is None:
        print("[gamemind daemon stop] no PID file — daemon is not running")
        return 0
    if not is_process_alive(pid):
        print(f"[gamemind daemon stop] stale PID {pid} — removing PID file")
        with contextlib.suppress(OSError):
            DEFAULT_PID_FILE.unlink()
        return 0

    try:
        if sys.platform == "win32":
            # Windows: CTRL_BREAK_EVENT works for console-attached processes.
            # If uvicorn is running in the foreground, this triggers its
            # graceful shutdown. SIGTERM isn't a real signal on Windows.
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"[gamemind daemon stop] sent shutdown signal to pid={pid}")
        return 0
    except (OSError, PermissionError) as exc:
        print(f"[gamemind daemon stop] failed to signal pid={pid}: {exc}")
        print("  (if the daemon is running in the foreground, Ctrl+C it directly)")
        return 1


def _cmd_daemon(args: argparse.Namespace) -> int:
    # Default to 127.0.0.1:8766 per Amendment A3.
    daemon_host = "127.0.0.1"
    daemon_port = 8766

    if args.daemon_cmd == "start":
        return _cmd_daemon_start(daemon_host, daemon_port)
    if args.daemon_cmd == "status":
        return _cmd_daemon_status(daemon_host, daemon_port)
    if args.daemon_cmd == "stop":
        return _cmd_daemon_stop(daemon_host, daemon_port)
    return 2


def _cmd_doctor(args: argparse.Namespace) -> int:
    modes: list[str] = []
    if args.all:
        modes = ["capture", "input", "live-perception"]
    else:
        if args.capture:
            modes.append("capture")
        if args.input:
            modes.append("input")
        if args.live_perception:
            modes.append("live-perception")
    if not modes:
        print(
            "gamemind doctor: pick at least one of --capture / --input / --live-perception / --all"
        )
        return 2
    print(f"[gamemind doctor] modes: {', '.join(modes)}")
    print("  TODO: implement each doctor sub-check in follow-up commits")
    print("  remediation table (DX-SUB-2):")
    print("    (a) Ollama down       → `ollama serve`")
    print("    (b) model not pulled  → `ollama pull qwen3-vl:8b-instruct-q4_K_M`")
    print("    (c) API key missing   → set ANTHROPIC_API_KEY env var")
    print("    (d) no game window    → focus the target game within 10s")
    print("    (e) wrong HWND picked → use --window-title filter")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    if not args.adapter.exists():
        print(f"gamemind run: adapter YAML not found at {args.adapter}")
        return 2
    print(f"[gamemind run] adapter={args.adapter} task={args.task!r}")
    print("  TODO: session manager / perception daemon / brain wakes in Step 3")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "daemon":
        return _cmd_daemon(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "run":
        return _cmd_run(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
