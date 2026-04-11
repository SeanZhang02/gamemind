"""GameMind FastAPI daemon entrypoint.

Phase C Step 1 iter-5: lifespan split into `gamemind.daemon.lifespan`,
keeping this file focused on routes + middleware. `/healthz` is the
only route currently exposed; `/v1/*` endpoints land in subsequent
iterations alongside the CLI wiring.
"""

from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gamemind.daemon.lifespan import check_ollama, lifespan


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
