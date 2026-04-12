"""Tests for gamemind/brain/mock_backend.py."""

from __future__ import annotations

from gamemind.brain.backend import LLMResponse
from gamemind.brain.mock_backend import MockBrainBackend


def _make_response(text: str = "ok", parsed: dict | None = None) -> LLMResponse:
    return LLMResponse(
        text=text,
        parsed_json=parsed,
        prompt_tokens=100,
        completion_tokens=50,
        cost_estimate_usd=0.005,
        latency_ms=0.0,
        request_id="",
        cached_system=False,
    )


def test_returns_scripted_responses() -> None:
    r1 = _make_response("first", {"plan": ["a"]})
    r2 = _make_response("second", {"verify_ok": True})
    mock = MockBrainBackend(scripted=[r1, r2])

    resp1 = mock.chat(
        [{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        cache_system=False,
        request_id="req-1",
    )
    assert resp1.text == "first"
    assert resp1.request_id == "req-1"
    assert resp1.parsed_json == {"plan": ["a"]}

    resp2 = mock.chat(
        [{"role": "user", "content": "verify"}],
        temperature=0.0,
        max_tokens=100,
        cache_system=False,
        request_id="req-2",
    )
    assert resp2.text == "second"
    assert resp2.request_id == "req-2"


def test_exhausted_returns_empty() -> None:
    mock = MockBrainBackend(scripted=[])
    resp = mock.chat(
        [{"role": "user", "content": "?"}],
        temperature=0.0,
        max_tokens=100,
        cache_system=False,
        request_id="req-x",
    )
    assert resp.text == ""
    assert resp.backend_meta.get("error") == "mock_exhausted"


def test_call_count_tracked() -> None:
    mock = MockBrainBackend(scripted=[_make_response(), _make_response()])
    mock.chat([], temperature=0, max_tokens=1, cache_system=False, request_id="a")
    mock.chat([], temperature=0, max_tokens=1, cache_system=False, request_id="b")
    assert mock.call_count == 2
    assert len(mock.calls) == 2


def test_satisfies_protocol() -> None:
    from gamemind.brain.backend import LLMBackend

    mock: LLMBackend = MockBrainBackend()
    assert mock is not None
