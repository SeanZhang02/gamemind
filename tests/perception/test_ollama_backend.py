"""Unit tests for OllamaBackend — all network calls are mocked via httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from gamemind.brain.backend import LLMResponse
from gamemind.perception.ollama_backend import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    OllamaBackend,
)


def _mock_ollama_chat_response(content: str = '{"block": "oak_log"}') -> dict:
    return {
        "model": DEFAULT_MODEL,
        "message": {"role": "assistant", "content": content},
        "total_duration": 1_200_000_000,
        "eval_count": 42,
        "prompt_eval_count": 128,
        "done": True,
    }


def test_defaults() -> None:
    assert DEFAULT_HOST == "http://127.0.0.1:11434"
    assert DEFAULT_MODEL == "gemma4:26b-a4b-it-q4_K_M"
    assert DEFAULT_NUM_CTX == 4096


def test_num_ctx_guard_rejects_large_without_explicit() -> None:
    with pytest.raises(ValueError, match="explicit_long_context"):
        OllamaBackend(num_ctx=16384)


def test_num_ctx_guard_accepts_large_with_explicit() -> None:
    b = OllamaBackend(num_ctx=16384, explicit_long_context=True)
    assert b.num_ctx == 16384
    b.close()


def test_chat_happy_path_with_transport_mock() -> None:
    """Use httpx MockTransport — the cleanest way to inject fake responses."""
    fake_body = _mock_ollama_chat_response('{"block": "oak_log", "count": 3}')

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json=fake_body)

    backend = OllamaBackend()
    # Swap the httpx.Client for one using MockTransport
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))

    response = backend.chat(
        messages=[{"role": "user", "content": "What's in front of me?"}],
        temperature=0.0,
        max_tokens=256,
        cache_system=False,
        request_id="tick-0001",
    )

    assert isinstance(response, LLMResponse)
    assert response.parsed_json == {"block": "oak_log", "count": 3}
    assert response.text == '{"block": "oak_log", "count": 3}'
    assert response.request_id == "tick-0001"
    assert response.cached_system is False
    assert response.cost_estimate_usd == 0.0
    assert response.prompt_tokens == 128
    assert response.completion_tokens == 42
    assert response.backend_meta["backend"] == "ollama"
    assert response.backend_meta["total_duration_ns"] == 1_200_000_000
    assert response.backend_meta["think_leaked"] is False
    backend.close()


def test_chat_returns_error_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    backend = OllamaBackend()
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))

    response = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=0.0,
        max_tokens=256,
        cache_system=False,
        request_id="tick-err",
    )

    assert response.text == ""
    assert response.parsed_json is None
    assert response.backend_meta["error"] == "ConnectError"
    assert "Connection refused" in response.backend_meta["error_msg"]
    assert response.request_id == "tick-err"
    backend.close()


def test_chat_returns_error_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    backend = OllamaBackend()
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))

    response = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=0.0,
        max_tokens=256,
        cache_system=False,
        request_id="tick-5xx",
    )

    assert response.text == ""
    assert response.parsed_json is None
    assert response.backend_meta.get("error") is not None
    backend.close()


def test_chat_parses_plain_text_fallback() -> None:
    """If the model returns non-JSON, parsed_json=None but text is populated."""
    fake_body = _mock_ollama_chat_response("this is not json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fake_body)

    backend = OllamaBackend()
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))

    response = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=0.0,
        max_tokens=256,
        cache_system=False,
        request_id="tick-text",
    )

    assert response.text == "this is not json"
    assert response.parsed_json is None
    backend.close()


def test_chat_detects_think_leak() -> None:
    fake_body = _mock_ollama_chat_response('<think>reasoning</think>{"block": "stone"}')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fake_body)

    backend = OllamaBackend()
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))

    response = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=0.0,
        max_tokens=256,
        cache_system=False,
        request_id="tick-think",
    )

    assert response.backend_meta["think_leaked"] is True
    backend.close()


def test_chat_echoes_request_id() -> None:
    fake_body = _mock_ollama_chat_response()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fake_body)

    backend = OllamaBackend()
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))

    for req_id in ["tick-a", "tick-b", "tick-c"]:
        response = backend.chat(
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=256,
            cache_system=False,
            request_id=req_id,
        )
        assert response.request_id == req_id
    backend.close()


def test_context_manager_closes_client() -> None:
    with OllamaBackend() as b:
        assert b._client is not None
    # After __exit__, close() has been called; no assertion possible on
    # httpx.Client's closed state directly, but calling close() twice is safe.
    b.close()
