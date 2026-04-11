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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
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


OLLAMA_HOST_ENV = "GAMEMIND_OLLAMA_HOST"
OLLAMA_MODEL_ENV = "GAMEMIND_OLLAMA_MODEL"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3-vl:8b-instruct-q4_K_M"


async def _check_ollama(host: str, model: str) -> tuple[bool, bool]:
    """Ollama liveness probe for /healthz (Amendment A6 partial).

    Returns (reachable, model_loaded). Never raises: on any HTTP or parse
    error, returns (False, False). This is intentionally permissive so
    /healthz stays snappy and never blocks on a dead Ollama.
    """
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{host}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            tags = {entry.get("name", "") for entry in data.get("models", [])}
            return True, model in tags
    except (httpx.HTTPError, ValueError, KeyError):
        return False, False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Daemon lifecycle per Amendment A3 + A6 partial.

    Phase C Step 1 scope:
      - Generate/load session token (Amendment A3)
      - Probe Ollama liveness + model loaded (Amendment A6 partial)

    Still TODO (next commit on this branch):
      - DPI awareness via Windows ctypes SetProcessDpiAwareness call
      - `gamemind/daemon/lifespan.py` split from this file
      - Ollama warmup with the probe/client.py two-phase pattern
    """
    app.state.session_token = _get_or_create_session_token()
    app.state.ollama_host = os.environ.get(OLLAMA_HOST_ENV, DEFAULT_OLLAMA_HOST)
    app.state.ollama_model = os.environ.get(OLLAMA_MODEL_ENV, DEFAULT_OLLAMA_MODEL)
    reachable, loaded = await _check_ollama(app.state.ollama_host, app.state.ollama_model)
    app.state.ollama_reachable = reachable
    app.state.model_loaded = loaded
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
    reachable, loaded = await _check_ollama(host, model)
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
