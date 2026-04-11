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


def _cmd_daemon(args: argparse.Namespace) -> int:
    if args.daemon_cmd == "start":
        # Late import so `gamemind --version` doesn't pull in uvicorn.
        import uvicorn  # noqa: PLC0415

        from gamemind.daemon.main import app  # noqa: PLC0415

        # Per Amendment A3: bind ONLY to 127.0.0.1. Never 0.0.0.0.
        print("[gamemind daemon start] binding to http://127.0.0.1:8766")
        print("  (Ctrl+C to stop; /healthz is unauthenticated, /v1/* requires bearer token)")
        uvicorn.run(app, host="127.0.0.1", port=8766, log_level="info")
        return 0
    if args.daemon_cmd == "status":
        import httpx  # noqa: PLC0415

        try:
            response = httpx.get("http://127.0.0.1:8766/healthz", timeout=2.0)
            response.raise_for_status()
            print("[gamemind daemon status] UP:", response.json())
            return 0
        except httpx.HTTPError as exc:
            print(f"[gamemind daemon status] DOWN: {exc}")
            return 1
    if args.daemon_cmd == "stop":
        # Phase C Step 1 scaffold: no PID file yet, so stop is advisory.
        # Real PID-file-based stop lands in the next commit on this branch.
        print("[gamemind daemon stop] Ctrl+C the daemon process; PID-file stop in next commit")
        return 0
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
