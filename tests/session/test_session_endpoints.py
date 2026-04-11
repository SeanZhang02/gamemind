"""Integration tests for /v1/state, /v1/session/start, /v1/session/stop.

Uses FastAPI TestClient + a fresh SessionManager per test. The test
client bypasses the authentication middleware by sending the
Authorization header with the app.state.session_token.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gamemind.daemon.main import app
from gamemind.session import SessionManager


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Yield a TestClient with a fresh SessionManager on app.state.

    We set app.state.session_manager BEFORE the TestClient runs the
    lifespan (which would overwrite it) by using TestClient's context
    manager and patching inside the `with` block.
    """
    with TestClient(app) as c:
        # lifespan ran; overwrite with a fresh manager so tests don't
        # share state
        app.state.session_manager = SessionManager()
        # Publish the auto-generated session token to the client for auth
        c.headers = {"Authorization": f"Bearer {app.state.session_token}"}
        yield c


def test_v1_state_initial(client: TestClient) -> None:
    response = client.get("/v1/state")
    assert response.status_code == 200
    data = response.json()
    assert data["session"]["status"] == "idle"
    assert data["session"]["session_id"] is None
    assert "daemon" in data


def test_v1_session_start_happy_path(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    response = client.post(
        "/v1/session/start",
        json={
            "adapter_path": "adapters/minecraft.yaml",
            "task_description": "chop 3 oak logs",
            "runs_root": str(runs_root),
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["session_id"] is not None
    assert data["events_path"] is not None
    assert Path(data["events_path"]).exists()


def test_v1_session_start_twice_returns_409(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    payload = {
        "adapter_path": "a.yaml",
        "task_description": "t",
        "runs_root": str(runs_root),
    }
    r1 = client.post("/v1/session/start", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/v1/session/start", json=payload)
    assert r2.status_code == 409
    assert "already running" in r2.json()["detail"]


def test_v1_state_after_start_shows_session(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    client.post(
        "/v1/session/start",
        json={
            "adapter_path": "adapters/test.yaml",
            "task_description": "hello",
            "runs_root": str(runs_root),
        },
    )
    response = client.get("/v1/state")
    data = response.json()
    assert data["session"]["status"] == "running"
    assert data["session"]["session_id"] is not None
    assert data["session"]["adapter_path"].replace("\\", "/").endswith("adapters/test.yaml")
    assert data["session"]["task_description"] == "hello"
    assert data["session"]["events_path"] is not None


def test_v1_session_stop_success(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    client.post(
        "/v1/session/start",
        json={"adapter_path": "a.yaml", "task_description": "t", "runs_root": str(runs_root)},
    )
    response = client.post("/v1/session/stop", json={"outcome": "success"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "terminal"
    assert data["outcome"] == "success"


def test_v1_session_stop_without_active_returns_409(client: TestClient) -> None:
    response = client.post("/v1/session/stop", json={"outcome": "success"})
    assert response.status_code == 409
    assert "cannot terminate" in response.json()["detail"]


def test_v1_session_stop_unknown_outcome_returns_400(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    client.post(
        "/v1/session/start",
        json={"adapter_path": "a.yaml", "task_description": "t", "runs_root": str(runs_root)},
    )
    response = client.post("/v1/session/stop", json={"outcome": "not_a_real_outcome"})
    assert response.status_code == 400
    assert "unknown outcome" in response.json()["detail"]


def test_v1_session_stop_user_stopped(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    client.post(
        "/v1/session/start",
        json={"adapter_path": "a.yaml", "task_description": "t", "runs_root": str(runs_root)},
    )
    response = client.post("/v1/session/stop", json={"outcome": "user_stopped"})
    assert response.status_code == 200
    assert response.json()["outcome"] == "user_stopped"


def test_v1_state_rejects_unauthenticated(client: TestClient) -> None:
    """Bearer token middleware should 401 unauth requests to /v1/*."""
    # Drop the default Authorization header from the client entirely
    client.headers.pop("Authorization", None)
    response = client.get("/v1/state")
    assert response.status_code == 401


def test_v1_session_start_rejects_origin_header(client: TestClient) -> None:
    """Amendment A3: Origin header rejected with 403."""
    response = client.post(
        "/v1/session/start",
        json={"adapter_path": "a.yaml", "task_description": "t"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
