"""Daemon lifespan — FastAPI startup/shutdown hooks.

Split from `gamemind/daemon/main.py` to keep main.py focused on routes
and middleware. Phase C Step 1 iter-5 scope:

  - Session token generation + persistence (Amendment A3)
  - Ollama liveness probe (Amendment A6 partial)
  - Windows DPI awareness via ctypes (no-op on non-Windows)

The `lifespan()` async context manager is mounted on the FastAPI app
via `FastAPI(..., lifespan=lifespan)`. It runs startup logic before
serving the first request, and shutdown logic when the app is torn down.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from gamemind.session import SessionManager

SESSION_TOKEN_ENV = "GAMEMIND_SESSION_TOKEN"
OLLAMA_HOST_ENV = "GAMEMIND_OLLAMA_HOST"
OLLAMA_MODEL_ENV = "GAMEMIND_OLLAMA_MODEL"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3-vl:8b-instruct-q4_K_M"


def get_or_create_session_token() -> str:
    """Generate a per-launch session token per Amendment A3.

    The token is stored in an env var visible to this process tree so
    CLI clients (spawned as child processes) can read it and inject
    it into Authorization headers.
    """
    token = os.environ.get(SESSION_TOKEN_ENV)
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ[SESSION_TOKEN_ENV] = token
    return token


async def check_ollama(host: str, model: str, *, timeout_s: float = 1.0) -> tuple[bool, bool]:
    """Ollama liveness + model-loaded probe for /healthz.

    Returns (reachable, model_loaded). Never raises: on any HTTP or
    parse error, returns (False, False). This is intentionally
    permissive so /healthz stays snappy and never blocks on a dead
    Ollama. Caller decides what to do with the result.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(f"{host}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            tags = {entry.get("name", "") for entry in data.get("models", [])}
            return True, model in tags
    except (httpx.HTTPError, ValueError, KeyError):
        return False, False


def enable_dpi_awareness() -> bool:
    """Windows-only: opt into per-monitor DPI awareness via ctypes.

    Required so screen capture coordinates aren't silently downscaled
    on hi-DPI displays. No-op on non-Windows. Returns True iff the call
    succeeded OR the platform is non-Windows (benign skip).

    Uses `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`
    when available (Win10 1703+), falling back to `SetProcessDPIAware()`
    on older Windows versions.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes  # noqa: PLC0415

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            result = user32.SetProcessDpiAwarenessContext(-4)
            if result:
                return True
        # Fallback to older API
        if hasattr(user32, "SetProcessDPIAware"):
            return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        pass
    return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan — startup + shutdown hooks.

    Startup order:
      1. DPI awareness (idempotent, early)
      2. Session token generation
      3. Ollama probe (non-blocking — populates state even on failure)

    Shutdown: nothing yet. Later iterations add:
      - EventWriter.close() on session termination
      - Perception daemon thread join
      - HTTP client pool teardown
    """
    enable_dpi_awareness()
    app.state.session_token = get_or_create_session_token()
    app.state.session_manager = SessionManager()
    app.state.ollama_host = os.environ.get(OLLAMA_HOST_ENV, DEFAULT_OLLAMA_HOST)
    app.state.ollama_model = os.environ.get(OLLAMA_MODEL_ENV, DEFAULT_OLLAMA_MODEL)
    reachable, loaded = await check_ollama(app.state.ollama_host, app.state.ollama_model)
    app.state.ollama_reachable = reachable
    app.state.model_loaded = loaded
    yield
    # Shutdown hooks land in later iterations
