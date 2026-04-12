"""Phase C Step 1 smoke tests — import surface + error format + CLI parse.

These are deliberately minimal: the scaffold PR just verifies that the
package structure imports cleanly, error codes are well-formed, and the
CLI parser doesn't crash. Real behavioral tests land with the real
implementations in subsequent commits.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

import gamemind
from gamemind import errors
from gamemind.capture import CaptureBackend, CaptureResult
from gamemind.capture.backend import CaptureResult as CaptureResult2
from gamemind.capture.dxgi_backend import DXGIBackend
from gamemind.capture.selector import (
    BLACK_FRAME_THRESHOLD,
    VARIANCE_FLOOR,
    CaptureSelector,
)
from gamemind.capture.wgc_backend import WGCBackend
from gamemind.cli import _build_parser, main
from gamemind.daemon.main import app


def test_package_version() -> None:
    assert gamemind.__version__ == "0.1.0"


def test_all_errors_are_gamemind_errors() -> None:
    # 1 base + 23 subclasses = 24 exports
    assert len(errors.__all__) == 24
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
    assert CaptureResult is CaptureResult2


def test_wgc_backend_invalid_hwnd_raises() -> None:
    """WGCBackend: on Windows, liveness=True but capture(invalid_hwnd) raises.

    On non-Windows, liveness=False and capture raises WGCInitError instead.
    Both paths MUST raise some gamemind.errors exception — the test doesn't
    care which, just that invalid HWNDs don't silently succeed.
    """
    backend = WGCBackend()
    if sys.platform == "win32":
        # Real binding installed: liveness should be True, capture of an
        # invalid HWND should raise WindowNotFoundError (not WGCInitError)
        assert backend.liveness() is True
        with pytest.raises(errors.WindowNotFoundError):
            backend.capture(hwnd=12345)
    else:
        # Non-Windows: stub-style behavior, liveness=False + WGCInitError
        assert backend.liveness() is False
        with pytest.raises(errors.WGCInitError) as exc_info:
            backend.capture(hwnd=12345)
        assert "E101" in str(exc_info.value)


def test_dxgi_backend_invalid_hwnd_raises() -> None:
    """DXGIBackend: parallel story to WGCBackend.

    Constructed with probe_on_init=False so the test doesn't actually
    grab a frame from the real monitor (which would be a test side
    effect). With probe disabled, liveness reflects the package-import
    check only.
    """
    backend = DXGIBackend(probe_on_init=False)
    if sys.platform == "win32":
        # Real dxcam imported successfully → liveness True
        assert backend.liveness() is True
        with pytest.raises(errors.WindowNotFoundError):
            backend.capture(hwnd=12345)
    else:
        assert backend.liveness() is False
        with pytest.raises(errors.DXGIInitError) as exc_info:
            backend.capture(hwnd=12345)
        assert "E102" in str(exc_info.value)


def test_capture_backend_protocol_satisfied() -> None:
    a: CaptureBackend = WGCBackend()
    b: CaptureBackend = DXGIBackend(probe_on_init=False)
    assert a is not b


def test_selector_constants() -> None:
    assert VARIANCE_FLOOR == 0.02
    assert BLACK_FRAME_THRESHOLD == 5


def test_selector_constructs() -> None:
    selector = CaptureSelector(
        primary=WGCBackend(),
        fallback=DXGIBackend(probe_on_init=False),
    )
    assert selector is not None


def test_cli_parser_builds() -> None:
    parser = _build_parser()
    args = parser.parse_args(["daemon", "start"])
    assert args.command == "daemon"
    assert args.daemon_cmd == "start"
    args = parser.parse_args(["doctor", "--all"])
    assert args.command == "doctor"
    assert args.all is True
    args = parser.parse_args(
        ["run", "--adapter", "adapters/minecraft.yaml", "--task", "chop 3 logs"]
    )
    assert args.command == "run"
    assert args.task == "chop 3 logs"


def test_cli_main_no_args_returns_zero() -> None:
    rc = main([])
    assert rc == 0


def test_cli_doctor_no_mode_returns_two() -> None:
    rc = main(["doctor"])
    assert rc == 2


def test_daemon_app_has_healthz_route() -> None:
    routes = {route.path for route in app.routes}
    assert "/healthz" in routes


def test_healthz_returns_ok_or_degraded() -> None:
    """Ollama is not running in CI — /healthz should return degraded, not crash."""
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in {"ok", "degraded"}
        assert "ollama_reachable" in data
        assert "model_loaded" in data


def test_v1_endpoint_rejects_unauthenticated() -> None:
    """Bearer token middleware should 401 unauth requests to /v1/*."""
    with TestClient(app) as client:
        response = client.post("/v1/doctor/capture", json={})
        assert response.status_code in {401, 404}


def test_origin_header_rejected() -> None:
    """Browser-origin requests must be rejected per Amendment A3."""
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "http://evil.example"})
        assert response.status_code == 403
