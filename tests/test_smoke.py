"""Phase C Step 1 smoke tests — import surface + error format + CLI parse.

These are deliberately minimal: the scaffold PR just verifies that the
package structure imports cleanly, error codes are well-formed, and the
CLI parser doesn't crash. Real behavioral tests land with the real
implementations in subsequent commits.
"""

from __future__ import annotations

import pytest

import gamemind
from gamemind import errors
from gamemind.capture import CaptureBackend, CaptureResult
from gamemind.capture.backend import CaptureResult as CaptureResult2
from gamemind.capture.dxgi_backend import DXGIBackend
from gamemind.capture.selector import CaptureSelector, BLACK_FRAME_THRESHOLD, VARIANCE_FLOOR
from gamemind.capture.wgc_backend import WGCBackend
from gamemind.cli import _build_parser, main
from gamemind.daemon.main import app


def test_package_version() -> None:
    assert gamemind.__version__ == "0.1.0"


def test_all_errors_are_gamemind_errors() -> None:
    assert len(errors.__all__) == 24  # 1 base + 23 subclasses
    for name in errors.__all__:
        cls = getattr(errors, name)
        assert issubclass(cls, errors.GameMindError)


def test_error_codes_are_unique_and_well_formed() -> None:
    codes: set[str] = set()
    for name in errors.__all__:
        cls = getattr(errors, name)
        if cls is errors.GameMindError:
            assert cls.code == "E000"
            continue
        assert cls.code.startswith("E") and cls.code[1:].isdigit()
        assert cls.code not in codes, f"duplicate error code: {cls.code}"
        codes.add(cls.code)
    # 23 subclasses, codes E101-E123
    assert len(codes) == 23


def test_error_message_format() -> None:
    exc = errors.OllamaConnectionError(cause="connection refused", host="http://127.0.0.1:11434")
    msg = str(exc)
    assert "E106" in msg
    assert "Ollama" in msg
    assert "ollama serve" in msg
    assert "docs/errors.md#e106" in msg
    assert "host=" in msg


def test_capture_result_is_dataclass() -> None:
    result = CaptureResult(
        frame_bytes=b"stub",
        frame_age_ms=12.5,
        capture_backend="WGC",
        variance=0.5,
        width=1280,
        height=720,
    )
    assert result.frame_age_ms == 12.5
    assert result.capture_backend == "WGC"
    # Re-import alias check
    assert CaptureResult is CaptureResult2


def test_wgc_backend_stub_raises() -> None:
    backend = WGCBackend()
    assert backend.liveness() is False
    with pytest.raises(errors.WGCInitError) as exc_info:
        backend.capture(hwnd=12345)
    assert "E101" in str(exc_info.value)


def test_dxgi_backend_stub_raises() -> None:
    backend = DXGIBackend()
    assert backend.liveness() is False
    with pytest.raises(errors.DXGIInitError) as exc_info:
        backend.capture(hwnd=12345)
    assert "E102" in str(exc_info.value)


def test_capture_backend_protocol_satisfied() -> None:
    # Pyright / mypy structural check — runtime assertion is tautological
    _: CaptureBackend = WGCBackend()
    _2: CaptureBackend = DXGIBackend()
    assert _ is not _2


def test_selector_constants() -> None:
    assert VARIANCE_FLOOR == 0.02
    assert BLACK_FRAME_THRESHOLD == 5


def test_selector_constructs() -> None:
    selector = CaptureSelector(primary=WGCBackend(), fallback=DXGIBackend())
    assert selector is not None


def test_cli_parser_builds() -> None:
    parser = _build_parser()
    # daemon subcommand
    args = parser.parse_args(["daemon", "start"])
    assert args.command == "daemon"
    assert args.daemon_cmd == "start"
    # doctor --all
    args = parser.parse_args(["doctor", "--all"])
    assert args.command == "doctor"
    assert args.all is True
    # run
    args = parser.parse_args(["run", "--adapter", "adapters/minecraft.yaml", "--task", "chop 3 logs"])
    assert args.command == "run"
    assert args.task == "chop 3 logs"


def test_cli_main_no_args_prints_help_returns_zero() -> None:
    # No-args path prints help and exits 0 (not 2)
    rc = main([])
    assert rc == 0


def test_cli_doctor_no_mode_returns_two() -> None:
    rc = main(["doctor"])
    assert rc == 2


def test_daemon_app_has_healthz_route() -> None:
    routes = {route.path for route in app.routes}
    assert "/healthz" in routes
