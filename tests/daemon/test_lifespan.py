"""Unit tests for daemon lifespan hooks."""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest

from gamemind.daemon.lifespan import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    SESSION_TOKEN_ENV,
    check_ollama,
    enable_dpi_awareness,
    get_or_create_session_token,
)


def test_session_token_generated_on_first_call() -> None:
    """After clearing the env var, get_or_create generates a fresh token."""
    os.environ.pop(SESSION_TOKEN_ENV, None)
    token = get_or_create_session_token()
    assert len(token) >= 32  # urlsafe_32 produces ~43 chars
    assert token == os.environ[SESSION_TOKEN_ENV]
    os.environ.pop(SESSION_TOKEN_ENV, None)


def test_session_token_persisted_across_calls() -> None:
    """Subsequent calls return the same token."""
    os.environ.pop(SESSION_TOKEN_ENV, None)
    token1 = get_or_create_session_token()
    token2 = get_or_create_session_token()
    assert token1 == token2
    os.environ.pop(SESSION_TOKEN_ENV, None)


def test_session_token_respects_preset_env() -> None:
    """If the env var is set, we use it instead of generating new."""
    os.environ[SESSION_TOKEN_ENV] = "preset-token-for-test"
    try:
        assert get_or_create_session_token() == "preset-token-for-test"
    finally:
        os.environ.pop(SESSION_TOKEN_ENV, None)


def test_enable_dpi_awareness_never_raises() -> None:
    """Always returns a bool, never raises — non-Windows returns True (skip)."""
    result = enable_dpi_awareness()
    assert isinstance(result, bool)


def test_defaults_sanity() -> None:
    assert DEFAULT_OLLAMA_HOST == "http://127.0.0.1:11434"
    assert DEFAULT_OLLAMA_MODEL == "qwen3-vl:8b-instruct-q4_K_M"


@pytest.mark.asyncio
async def test_check_ollama_returns_false_on_connection_refused() -> None:
    """When Ollama isn't running (typical CI), check returns (False, False)."""
    # Point to a port that's unlikely to be open
    reachable, loaded = await check_ollama("http://127.0.0.1:1", DEFAULT_OLLAMA_MODEL)
    assert reachable is False
    assert loaded is False


@pytest.mark.asyncio
async def test_check_ollama_parses_model_list() -> None:
    """Mock httpx.AsyncClient.get to return a fake /api/tags response."""
    fake_response_data = {
        "models": [
            {"name": "qwen3-vl:8b-instruct-q4_K_M", "size": 6_200_000_000},
            {"name": "llama3:8b", "size": 4_800_000_000},
        ]
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return fake_response_data

    async def fake_get(self, url: str) -> FakeResponse:
        assert "/api/tags" in url
        return FakeResponse()

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        reachable, loaded = await check_ollama(DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL)
        assert reachable is True
        assert loaded is True


@pytest.mark.asyncio
async def test_check_ollama_model_not_in_list() -> None:
    """Ollama reachable but our target model not pulled → (True, False)."""
    fake_data = {"models": [{"name": "llama3:8b"}]}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return fake_data

    async def fake_get(self, url: str) -> FakeResponse:
        return FakeResponse()

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        reachable, loaded = await check_ollama(DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL)
        assert reachable is True
        assert loaded is False


@pytest.mark.asyncio
async def test_check_ollama_http_error_returns_false() -> None:
    """Any HTTP error → (False, False), never raises."""

    async def fake_get(self, url: str):
        raise httpx.ConnectError("Connection refused")

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        reachable, loaded = await check_ollama(DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL)
        assert reachable is False
        assert loaded is False


def test_daemon_main_still_imports_app() -> None:
    """Regression: after the split, `gamemind.daemon.main.app` is still available."""
    from gamemind.daemon.main import app

    routes = {r.path for r in app.routes}
    assert "/healthz" in routes
