"""GameMind FastAPI daemon entrypoint.

Phase C Step 1 iter-5: lifespan split into `gamemind.daemon.lifespan`,
keeping this file focused on routes + middleware.
Phase C Step 1 iter-10: added /v1/state, /v1/session/start, /v1/session/stop
session endpoints backed by a `SessionManager` living on `app.state`.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gamemind.daemon.lifespan import check_ollama, lifespan
from gamemind.session import SessionManager
from gamemind.session.manager import NoActiveSessionError, SessionAlreadyRunningError


app = FastAPI(
    title="GameMind daemon",
    description="Universal game AI agent framework — see docs/final-design.md",
    version="0.1.0",
    lifespan=lifespan,
)

# SessionManager lives on app.state. The lifespan hook in
# gamemind.daemon.lifespan initializes app.state.session_manager
# alongside the session token, so requests can always read it.
# Tests that bypass the lifespan (e.g. TestClient without async startup)
# fall back to creating a manager here lazily — see _get_session_manager.


def _get_session_manager(request: Request) -> SessionManager:
    """Return the SessionManager for this request, creating if absent.

    In production, the lifespan hook pre-populates
    `app.state.session_manager`. In isolated tests that construct a
    TestClient without running the lifespan, the first accessor lazily
    creates one.
    """
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        manager = SessionManager()
        request.app.state.session_manager = manager
    return manager


class SessionStartRequest(BaseModel):
    """Body for POST /v1/session/start."""

    adapter_path: str
    task_description: str
    runs_root: str = "runs"


class SessionStopRequest(BaseModel):
    """Body for POST /v1/session/stop."""

    outcome: str = "user_stopped"


@app.middleware("http")
async def enforce_local_auth(request: Request, call_next):
    """Bearer token + Origin rejection per Amendment A3.

    /healthz is unauthenticated. Every /v1/* endpoint requires a valid
    Authorization header AND no Origin header (browser-originating request).
    """
    # Origin rejection: any request carrying an Origin header is browser-originated.
    if request.headers.get("origin"):
        return JSONResponse(
            {"detail": "browser origin requests are rejected by design (Amendment A3)"},
            status_code=403,
        )
    # /healthz is exempt from auth so external probes can check liveness.
    if request.url.path == "/healthz":
        return await call_next(request)
    # Everything else requires a bearer token.
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "missing bearer token"}, status_code=401)
    provided = auth.removeprefix("Bearer ").strip()
    expected = request.app.state.session_token
    if not secrets.compare_digest(provided, expected):
        return JSONResponse({"detail": "invalid bearer token"}, status_code=401)
    return await call_next(request)


@app.get("/v1/state")
async def v1_state(request: Request) -> dict:
    """Return daemon + session state. Authenticated by bearer token.

    Use this to check whether a session is running, what adapter it
    loaded, what events.jsonl path it's writing to, etc.
    """
    manager = _get_session_manager(request)
    info = manager.snapshot()
    return {
        "daemon": {
            "model_loaded": request.app.state.model_loaded,
            "ollama_reachable": request.app.state.ollama_reachable,
        },
        "session": {
            "session_id": info.session_id,
            "status": info.status,
            "adapter_path": info.adapter_path,
            "task_description": info.task_description,
            "outcome": info.outcome,
            "events_path": info.events_path,
            "started_at_monotonic_ns": info.started_at_monotonic_ns,
        },
    }


@app.post("/v1/session/start")
async def v1_session_start(body: SessionStartRequest, request: Request) -> dict:
    """Start a new session. Returns the snapshot on success.

    Returns 409 if a session is already running.
    """
    manager = _get_session_manager(request)
    try:
        info = manager.start(
            adapter_path=Path(body.adapter_path),
            task_description=body.task_description,
            runs_root=Path(body.runs_root),
        )
    except SessionAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "session_id": info.session_id,
        "status": info.status,
        "events_path": info.events_path,
    }


@app.post("/v1/session/stop")
async def v1_session_stop(body: SessionStopRequest, request: Request) -> dict:
    """Terminate the active session with a named outcome.

    Returns 409 if no session is running.
    Returns 400 if the outcome is not a recognized value.
    """
    manager = _get_session_manager(request)
    try:
        info = manager.transition_to_terminal(outcome=body.outcome)  # type: ignore[arg-type]
    except NoActiveSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "session_id": info.session_id,
        "status": info.status,
        "outcome": info.outcome,
        "events_path": info.events_path,
    }


@app.get("/healthz")
async def healthz(request: Request) -> dict:
    """Minimal liveness info per Amendment A3.

    Re-probes Ollama each call (cheap — 1s timeout) so /healthz reflects
    the current state, not the startup state. If Ollama crashes mid-session,
    /healthz goes from ok → degraded without needing daemon restart.

    Returns:
        {"status": "ok" | "degraded",
         "model_loaded": bool,
         "ollama_reachable": bool,
         "ollama_host": str,
         "ollama_model": str}
    """
    host = request.app.state.ollama_host
    model = request.app.state.ollama_model
    reachable, loaded = await check_ollama(host, model)
    request.app.state.ollama_reachable = reachable
    request.app.state.model_loaded = loaded
    return {
        "status": "ok" if (reachable and loaded) else "degraded",
        "model_loaded": loaded,
        "ollama_reachable": reachable,
        "ollama_host": host,
        "ollama_model": model,
    }


__all__ = ["app"]
