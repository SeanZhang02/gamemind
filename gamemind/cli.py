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
    run.add_argument(
        "--goal", type=str, default=None, help="Goal grammar name (default: inferred from task)"
    )
    run.add_argument("--window-title", default=None, help="Filter HWND by window title substring")
    run.add_argument("--dry-run", action="store_true", help="Use MockBrainBackend (no API cost)")
    run.add_argument(
        "--budget", type=float, default=0.30, help="Session brain API budget USD (default 0.30)"
    )
    run.add_argument(
        "--model", type=str, default="claude-sonnet-4-6", help="Anthropic model for Layer 3 brain"
    )

    # adapter
    adapter = sub.add_parser("adapter", help="Inspect or validate adapter YAML files")
    adapter_sub = adapter.add_subparsers(dest="adapter_cmd", required=True)
    adapter_validate = adapter_sub.add_parser(
        "validate", help="Validate an adapter YAML against the pydantic schema"
    )
    adapter_validate.add_argument("path", type=Path, help="Path to adapter YAML file")
    adapter_validate.add_argument(
        "--adapters-root",
        type=Path,
        default=None,
        help="Directory adapters must live under (default: ./adapters)",
    )

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


def _print_remediation_table() -> None:
    print("  remediation table (DX-SUB-2):")
    print("    (a) Ollama down       → `ollama serve`")
    print("    (b) model not pulled  → `ollama pull gemma4:26b-a4b-it-q4_K_M`")
    print("    (c) API key missing   → set ANTHROPIC_API_KEY env var")
    print("    (d) no game window    → focus the target game within 10s")
    print("    (e) wrong HWND picked → use --window-title filter")


def _find_target_hwnd(window_title_filter: str | None) -> tuple[int, str]:
    """Find a target HWND for doctor --capture.

    If `window_title_filter` is provided, match any top-level visible
    window whose title contains that substring (case-insensitive).
    Otherwise return the current foreground window.

    Returns (hwnd, title). hwnd == 0 if nothing matched.
    """
    import ctypes  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    if sys.platform != "win32":
        return 0, ""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    def _get_title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
        return buf.value

    if window_title_filter is None:
        raw = user32.GetForegroundWindow()
        if not raw:
            return 0, ""
        hwnd = int(raw)
        return hwnd, _get_title(hwnd)

    # EnumWindows + substring match. WNDENUMPROC is a Win32-convention
    # uppercase callback type name; N806 silenced.
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_void_p)  # noqa: N806
    user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]
    user32.EnumWindows.restype = wintypes.BOOL

    found: list[tuple[int, str]] = []
    filter_lower = window_title_filter.lower()

    def cb(hwnd_raw: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(wintypes.HWND(hwnd_raw)):
            return True
        title = _get_title(hwnd_raw)
        if filter_lower in title.lower():
            found.append((int(hwnd_raw), title))
            return False  # stop enum
        return True

    user32.EnumWindows(WNDENUMPROC(cb), None)
    if not found:
        return 0, ""
    return found[0]


def _cmd_doctor_capture(window_title_filter: str | None) -> int:
    """Real `gamemind doctor --capture`: grab one frame, save to disk, report."""
    import sys  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415

    from gamemind.capture.dxgi_backend import DXGIBackend  # noqa: PLC0415
    from gamemind.capture.wgc_backend import WGCBackend  # noqa: PLC0415

    if sys.platform != "win32":
        print("[gamemind doctor --capture] not supported on non-Windows")
        return 2

    hwnd, title = _find_target_hwnd(window_title_filter)
    if hwnd == 0:
        if window_title_filter:
            print(
                f"[gamemind doctor --capture] no window matched --window-title {window_title_filter!r}"
            )
        else:
            print("[gamemind doctor --capture] no foreground window found")
        _print_remediation_table()
        return 1
    print(f"[gamemind doctor --capture] target HWND={hwnd} title={title!r}")

    runs_dir = Path("runs")
    runs_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Try WGC first, then DXGI fallback if WGC fails
    wgc_result = None
    wgc_error: str | None = None
    try:
        wgc = WGCBackend()
        if wgc.liveness():
            wgc_result = wgc.capture(hwnd=hwnd, timeout_ms=2000)
    except Exception as exc:  # noqa: BLE001
        wgc_error = f"{type(exc).__name__}: {exc}"

    if wgc_result is not None:
        out_path = runs_dir / f"doctor-wgc-{ts}.webp"
        out_path.write_bytes(wgc_result.frame_bytes)
        print("[gamemind doctor --capture] WGC OK")
        print(f"  size:     {wgc_result.width}x{wgc_result.height}")
        print(f"  variance: {wgc_result.variance:.6f} (floor 0.02)")
        print(f"  age_ms:   {wgc_result.frame_age_ms:.1f}")
        print(f"  bytes:    {len(wgc_result.frame_bytes)}")
        print(f"  saved:    {out_path.resolve()}")
        return 0

    print(f"[gamemind doctor --capture] WGC failed: {wgc_error}")
    print("[gamemind doctor --capture] trying DXGI fallback...")

    dxgi_result = None
    dxgi_error: str | None = None
    try:
        dxgi = DXGIBackend()
        if not dxgi.liveness():
            dxgi_error = f"DXGIBackend unhealthy: {dxgi._init_error}"
        else:
            dxgi_result = dxgi.capture(hwnd=hwnd, timeout_ms=2000)
    except Exception as exc:  # noqa: BLE001
        dxgi_error = f"{type(exc).__name__}: {exc}"

    if dxgi_result is not None:
        out_path = runs_dir / f"doctor-dxgi-{ts}.webp"
        out_path.write_bytes(dxgi_result.frame_bytes)
        print("[gamemind doctor --capture] DXGI OK (WGC unavailable)")
        print(f"  size:     {dxgi_result.width}x{dxgi_result.height}")
        print(f"  variance: {dxgi_result.variance:.6f}")
        print(f"  age_ms:   {dxgi_result.frame_age_ms:.1f}")
        print(f"  bytes:    {len(dxgi_result.frame_bytes)}")
        print(f"  saved:    {out_path.resolve()}")
        return 0

    print(f"[gamemind doctor --capture] DXGI failed: {dxgi_error}")
    print("[gamemind doctor --capture] BOTH BACKENDS FAILED")
    _print_remediation_table()
    return 1


def _cmd_doctor_input() -> int:
    """Real `gamemind doctor --input`: verify PyDirectInputBackend liveness + ImmDisableIME."""
    import sys  # noqa: PLC0415

    from gamemind.input.pydirectinput_backend import PyDirectInputBackend  # noqa: PLC0415

    if sys.platform != "win32":
        print("[gamemind doctor --input] not supported on non-Windows")
        return 2

    backend = PyDirectInputBackend()
    if not backend.liveness():
        print(f"[gamemind doctor --input] PyDirectInputBackend unhealthy: {backend._init_error}")
        _print_remediation_table()
        return 1
    print("[gamemind doctor --input] PyDirectInputBackend OK (pydirectinput-rgx imported)")
    print("  note: real end-to-end input test requires a live target window")
    print("  note: run `python scripts/test_input_live.py` for tkinter-surrogate integration test")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    modes: list[str] = []
    if args.all:
        modes = ["capture", "input"]
        # live-perception stays stub until the Amendment A1 live spike
        # lands in Batch B (needs a running game window)
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
    overall_rc = 0
    if "capture" in modes:
        rc = _cmd_doctor_capture(args.window_title)
        if rc != 0:
            overall_rc = rc
    if "input" in modes:
        rc = _cmd_doctor_input()
        if rc != 0:
            overall_rc = rc
    if "live-perception" in modes:
        print(
            "[gamemind doctor --live-perception] STUB — Amendment A1 spike needs a running game window"
        )
        print("  (Batch B work, requires live Minecraft / Stardew / Dead Cells)")
    return overall_rc


def _cmd_run(args: argparse.Namespace) -> int:
    """Wire `gamemind run --adapter --task` to AgentRunner."""
    import sys  # noqa: PLC0415

    if sys.platform != "win32" and not args.dry_run:
        print(
            "[gamemind run] real mode requires win32 (WGC + pydirectinput). Use --dry-run on other platforms."
        )
        return 2

    if not args.adapter.exists():
        print(f"[gamemind run] adapter YAML not found at {args.adapter}")
        return 2

    from gamemind.adapter.loader import load  # noqa: PLC0415
    from gamemind.brain.backend import LLMResponse  # noqa: PLC0415
    from gamemind.brain.budget_tracker import BudgetExceededError  # noqa: PLC0415
    from gamemind.brain.mock_backend import MockBrainBackend  # noqa: PLC0415
    from gamemind.events.writer import EventWriter  # noqa: PLC0415
    from gamemind.runner import AgentRunner, RunnerConfig  # noqa: PLC0415
    from gamemind.session.manager import SessionManager  # noqa: PLC0415

    try:
        adapter = load(args.adapter)
    except Exception as exc:  # noqa: BLE001
        print(f"[gamemind run] adapter load error: {exc}")
        return 1

    goal_name = args.goal
    if goal_name is None:
        goal_names = list(adapter.goal_grammars.keys())
        if len(goal_names) == 1:
            goal_name = goal_names[0]
        else:
            print(f"[gamemind run] multiple goals available: {goal_names}. Use --goal to pick one.")
            return 2

    print(f"[gamemind run] adapter={adapter.display_name} task={args.task!r} goal={goal_name}")
    print(f"  dry_run={args.dry_run} budget=${args.budget:.2f} model={args.model}")

    runs_root = Path("runs")
    runs_root.mkdir(exist_ok=True)

    session_manager = SessionManager()
    session_dir = runs_root / f"run-{int(__import__('time').time())}"
    event_writer = EventWriter(session_dir)
    event_writer.start()

    session_info = session_manager.start(
        adapter_path=args.adapter,
        task_description=args.task,
        runs_root=runs_root,
    )
    print(f"  session_id={session_info.session_id}")

    if args.dry_run:
        brain = MockBrainBackend(
            scripted=[
                LLMResponse(
                    text='{"plan": ["approach_target", "interact", "verify"]}',
                    parsed_json={"plan": ["approach_target", "interact", "verify"]},
                    prompt_tokens=500,
                    completion_tokens=100,
                    cost_estimate_usd=0.0,
                    latency_ms=0.0,
                    request_id="",
                    cached_system=False,
                ),
                LLMResponse(
                    text='{"verify_ok": true}',
                    parsed_json={"verify_ok": True},
                    prompt_tokens=600,
                    completion_tokens=20,
                    cost_estimate_usd=0.0,
                    latency_ms=0.0,
                    request_id="",
                    cached_system=False,
                ),
            ]
        )
        perception = MockBrainBackend(
            scripted=[
                LLMResponse(
                    text='{"inventory": {"log": 3}}',
                    parsed_json={"inventory": {"log": 3}},
                    prompt_tokens=200,
                    completion_tokens=30,
                    cost_estimate_usd=0.0,
                    latency_ms=0.0,
                    request_id="",
                    cached_system=False,
                ),
            ]
            * 100
        )
        from gamemind.capture.backend import CaptureResult  # noqa: PLC0415

        class _MockCapture:
            def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult:
                import io  # noqa: PLC0415
                from PIL import Image  # noqa: PLC0415

                img = Image.new("RGB", (64, 64), (100, 100, 100))
                buf = io.BytesIO()
                img.save(buf, format="WEBP")
                return CaptureResult(
                    frame_bytes=buf.getvalue(),
                    frame_age_ms=50.0,
                    capture_backend="mock",
                    variance=0.5,
                    width=64,
                    height=64,
                )

            def liveness(self) -> bool:
                return True

        capture = _MockCapture()
        hwnd = 0
        input_backend = None
    else:
        from gamemind.brain.anthropic_backend import AnthropicBackend  # noqa: PLC0415
        from gamemind.brain.prompt_assembler import BASE_SYSTEM_PROMPT  # noqa: PLC0415
        from gamemind.capture.wgc_backend import WGCBackend  # noqa: PLC0415

        hwnd_val, title = _find_target_hwnd(args.window_title)
        if hwnd_val == 0:
            print(f"[gamemind run] no window matched --window-title {args.window_title!r}")
            return 1
        print(f"  HWND={hwnd_val} title={title!r}")
        hwnd = hwnd_val

        capture = WGCBackend()
        if not capture.liveness():
            print("[gamemind run] WGCBackend unhealthy")
            return 1

        brain = AnthropicBackend(system=BASE_SYSTEM_PROMPT, model=args.model)
        from gamemind.perception.ollama_backend import OllamaBackend  # noqa: PLC0415

        perception = OllamaBackend()
        print("[gamemind run] warming up Ollama VLM...")
        try:
            text_ms, vision_ms = perception.warmup()
            print(f"  warmup done: text={text_ms:.0f}ms vision={vision_ms:.0f}ms")
        except Exception as exc:  # noqa: BLE001
            print(f"  warmup failed (non-fatal): {exc}")
        from gamemind.input.pydirectinput_backend import PyDirectInputBackend  # noqa: PLC0415

        input_backend = PyDirectInputBackend()

    config = RunnerConfig(
        adapter=adapter,
        task=args.task,
        goal_name=goal_name,
        runs_root=runs_root,
        capture=capture,
        perception=perception,
        brain=brain,
        input=input_backend,
        hwnd=hwnd,
        budget_usd=args.budget,
        dry_run=args.dry_run,
    )

    try:
        runner = AgentRunner(config, session_manager, event_writer)
        outcome = runner.run()
    except BudgetExceededError as e:
        print(f"[gamemind run] BUDGET EXCEEDED: {e}")
        outcome = "runaway"
    except KeyboardInterrupt:
        print("[gamemind run] interrupted by user")
        outcome = "user_stopped"
    except Exception as exc:  # noqa: BLE001
        import traceback  # noqa: PLC0415

        print(f"[gamemind run] unhandled error: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        outcome = "unhandled_exception"

    session_manager.transition_to_terminal(outcome=outcome)
    event_writer.close()

    print(f"[gamemind run] session ended: outcome={outcome}")
    print(f"  events: {session_info.events_path}")
    return 0 if outcome == "success" else 1


def _cmd_adapter(args: argparse.Namespace) -> int:
    """Handle `gamemind adapter <subcommand>`."""
    if args.adapter_cmd == "validate":
        return _cmd_adapter_validate(args.path, args.adapters_root)
    return 2


def _cmd_adapter_validate(path: Path, adapters_root: Path | None) -> int:
    """Run `gamemind.adapter.loader.validate()` and print results.

    Exit codes:
      0 — valid, no errors
      1 — invalid, errors printed
      2 — file not found
    """
    # Late import so `gamemind --version` doesn't pull in pydantic/yaml
    from gamemind.adapter.loader import load, validate  # noqa: PLC0415

    if not path.exists():
        print(f"[gamemind adapter validate] file not found: {path}")
        return 2

    errors = validate(path, adapters_root=adapters_root)
    if not errors:
        # Re-load to surface the actual model (for display)
        try:
            adapter = load(path, adapters_root=adapters_root)
        except Exception as exc:  # noqa: BLE001
            # validate() returned OK but load() raised — shouldn't happen,
            # but keep the CLI robust.
            print(f"[gamemind adapter validate] internal error: {exc}")
            return 1
        print(f"[gamemind adapter validate] OK: {path}")
        print(f"  display_name:    {adapter.display_name}")
        print(f"  schema_version:  {adapter.schema_version}")
        print(f"  actions:         {len(adapter.actions)} bindings")
        print(f"  goal_grammars:   {len(adapter.goal_grammars)} task templates")
        print(f"  world_facts:     {len(adapter.world_facts)} entries")
        print(f"  perception.hz:   {adapter.perception.tick_hz}")
        print(f"  freshness_ms:    {adapter.perception.freshness_budget_ms}")
        goal_names = ", ".join(sorted(adapter.goal_grammars.keys()))
        print(f"  goals:           {goal_names}")
        return 0

    print(f"[gamemind adapter validate] FAILED: {path}")
    for i, err in enumerate(errors, start=1):
        print(f"  [{i}] {err}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "daemon":
        return _cmd_daemon(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "adapter":
        return _cmd_adapter(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
