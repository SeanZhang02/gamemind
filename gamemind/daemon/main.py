"""GameMind FastAPI daemon entrypoint.

Phase C Step 1 scaffolds:
  - /healthz (unauthenticated, minimal info per Amendment A3)
  - bearer token auth middleware (generated per-launch)
  - Origin header rejection (browser CORS attack prevention)
  - Lifespan for graceful startup (Ollama liveness check stub)

Subsequent commits add:
  - POST /v1/doctor/capture
  - POST /v1/doctor/input
  - POST /v1/doctor/live-perception
  - POST /v1/session/start
  - POST /v1/action
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse


SESSION_TOKEN_ENV = "GAMEMIND_SESSION_TOKEN"


def _get_or_create_session_token() -> str:
    """Generate a per-launch session token per Amendment A3.

    The token is stored in an env var visible to this process tree so the
    CLI can inject it into Authorization headers on subsequent requests.
    """
    token = os.environ.get(SESSION_TOKEN_ENV)
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ[SESSION_TOKEN_ENV] = token
    return token


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Daemon lifecycle.

    Phase C Step 1 scope:
      - Generate/load session token
      - TODO next commit: warm Ollama + check model loaded (Amendment A6)
      - TODO: DPI awareness (Windows ctypes call)
    """
    app.state.session_token = _get_or_create_session_token()
    app.state.ollama_reachable = False  # wired up in next commit
    app.state.model_loaded = False      # wired up in next commit
    yield
    # Shutdown: nothing yet


app = FastAPI(
    title="GameMind daemon",
    description="Universal game AI agent framework — see docs/final-design.md",
    version="0.1.0",
    lifespan=lifespan,
)


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


@app.get("/healthz")
async def healthz(request: Request) -> dict:
    """Minimal liveness info per Amendment A3.

    Returns:
        {"status": "ok", "model_loaded": bool, "ollama_reachable": bool}
    """
    return {
        "status": "ok",
        "model_loaded": request.app.state.model_loaded,
        "ollama_reachable": request.app.state.ollama_reachable,
    }


__all__ = ["app"]
