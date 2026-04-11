"""Unit tests for AnthropicBackend — anthropic client mocked via monkeypatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import pytest

from gamemind.brain.anthropic_backend import (
    DEFAULT_MODEL,
    ENV_API_KEY,
    AnthropicBackend,
    _estimate_cost_usd,
)
from gamemind.brain.backend import LLMResponse


@dataclass
class FakeTextBlock:
    type: str
    text: str


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeMessage:
    content: list[Any]
    usage: FakeUsage
    stop_reason: str = "end_turn"


class FakeStream:
    """Mimics the context-manager stream returned by client.messages.stream()."""

    def __init__(self, final_message: FakeMessage) -> None:
        self._final_message = final_message

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_final_message(self) -> FakeMessage:
        return self._final_message


class FakeMessages:
    def __init__(
        self, final_message: FakeMessage | None = None, raise_exc: Exception | None = None
    ) -> None:
        self._final_message = final_message
        self._raise_exc = raise_exc
        self.call_count = 0
        self.last_kwargs: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> FakeStream:
        self.call_count += 1
        self.last_kwargs = kwargs
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._final_message is not None
        return FakeStream(self._final_message)


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-fake-test-key-for-ci-xxxxxxxxxxxxxxxxxx")


def _build_backend(
    fake_messages: FakeMessages, monkeypatch: pytest.MonkeyPatch
) -> AnthropicBackend:
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-fake-test-key-for-ci-xxxxxxxxxxxxxxxxxx")
    backend = AnthropicBackend(system="You are a test agent.", model=DEFAULT_MODEL)
    # Inject the fake client
    backend._client = FakeClient(fake_messages)  # type: ignore[assignment]
    return backend


def test_defaults() -> None:
    assert DEFAULT_MODEL == "claude-opus-4-6"
    assert ENV_API_KEY == "ANTHROPIC_API_KEY"


def test_init_refuses_without_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicBackend(system="x")


def test_init_accepts_explicit_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    backend = AnthropicBackend(system="x", api_key="sk-ant-explicit-test-key-xxxxxxxxxxxxx")
    assert backend.system == "x"
    assert backend.model == DEFAULT_MODEL
    backend.close()


def test_chat_happy_path_with_text_response(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text="Hello from Claude")],
        usage=FakeUsage(input_tokens=100, output_tokens=20),
    )
    fake_messages = FakeMessages(final_message=final)
    backend = _build_backend(fake_messages, monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "Hi"}],
        temperature=1.0,
        max_tokens=16000,
        cache_system=True,
        request_id="test-001",
    )

    assert isinstance(result, LLMResponse)
    assert result.text == "Hello from Claude"
    assert result.parsed_json is None  # not JSON
    assert result.request_id == "test-001"
    assert result.cached_system is False  # no cache hit on first call
    assert result.backend_meta["backend"] == "anthropic"
    assert result.backend_meta["model"] == DEFAULT_MODEL
    assert result.backend_meta["stop_reason"] == "end_turn"
    assert fake_messages.call_count == 1


def test_chat_passes_through_adaptive_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text="ok")],
        usage=FakeUsage(input_tokens=10, output_tokens=5),
    )
    fake_messages = FakeMessages(final_message=final)
    backend = _build_backend(fake_messages, monkeypatch)

    backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-adaptive",
    )

    kwargs = fake_messages.last_kwargs
    assert kwargs is not None
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["temperature"] == 1.0
    assert kwargs["max_tokens"] == 1000


def test_chat_cache_system_true_wraps_system_block(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text="ok")],
        usage=FakeUsage(input_tokens=10, output_tokens=5),
    )
    fake_messages = FakeMessages(final_message=final)
    backend = _build_backend(fake_messages, monkeypatch)

    backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-cache",
    )

    kwargs = fake_messages.last_kwargs
    assert kwargs is not None
    system = kwargs["system"]
    assert isinstance(system, list)
    assert len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["text"] == "You are a test agent."
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_chat_cache_system_false_passes_raw_string(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text="ok")],
        usage=FakeUsage(input_tokens=10, output_tokens=5),
    )
    fake_messages = FakeMessages(final_message=final)
    backend = _build_backend(fake_messages, monkeypatch)

    backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=False,
        request_id="test-nocache",
    )

    kwargs = fake_messages.last_kwargs
    assert kwargs is not None
    assert kwargs["system"] == "You are a test agent."


def test_chat_parses_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text='{"goal": "chop 3 logs", "confidence": 0.8}')],
        usage=FakeUsage(input_tokens=100, output_tokens=30),
    )
    backend = _build_backend(FakeMessages(final_message=final), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "plan"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-json",
    )

    assert result.parsed_json == {"goal": "chop 3 logs", "confidence": 0.8}
    assert result.text == '{"goal": "chop 3 logs", "confidence": 0.8}'


def test_chat_handles_cache_read_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text="ok")],
        usage=FakeUsage(
            input_tokens=50,
            output_tokens=10,
            cache_read_input_tokens=5000,
            cache_creation_input_tokens=0,
        ),
    )
    backend = _build_backend(FakeMessages(final_message=final), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-cachehit",
    )

    assert result.cached_system is True
    assert result.backend_meta["cache_read_input_tokens"] == 5000
    assert result.backend_meta["uncached_input_tokens"] == 50
    assert result.prompt_tokens == 5050  # cached + uncached input


def test_chat_concatenates_multiple_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[
            FakeTextBlock(type="text", text="First part. "),
            FakeTextBlock(type="text", text="Second part."),
        ],
        usage=FakeUsage(input_tokens=20, output_tokens=10),
    )
    backend = _build_backend(FakeMessages(final_message=final), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-multi-block",
    )

    assert result.text == "First part. Second part."


def test_chat_ignores_non_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class FakeThinking:
        type: str
        thinking: str

    final = FakeMessage(
        content=[
            FakeThinking(type="thinking", thinking="let me think..."),
            FakeTextBlock(type="text", text="Answer here."),
        ],
        usage=FakeUsage(input_tokens=20, output_tokens=50),
    )
    backend = _build_backend(FakeMessages(final_message=final), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-thinking",
    )

    assert result.text == "Answer here."


def _make_status_error(status_code: int, message: str) -> anthropic.APIStatusError:
    """Build an APIStatusError with a minimal request/response pair."""
    import httpx  # noqa: PLC0415

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status_code, request=request, text=message)
    return anthropic.APIStatusError(message, response=response, body=None)


def test_chat_handles_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = anthropic.RateLimitError(
        "rate limited",
        response=_make_status_error(429, "rate limited").response,
        body=None,
    )
    backend = _build_backend(FakeMessages(raise_exc=exc), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-429",
    )

    assert result.text == ""
    assert result.parsed_json is None
    assert result.backend_meta["error"] == "rate_limit"


def test_chat_handles_5xx_as_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = _make_status_error(500, "internal server error")
    backend = _build_backend(FakeMessages(raise_exc=exc), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-5xx",
    )

    assert result.backend_meta["error"] == "service_error"


def test_chat_handles_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx  # noqa: PLC0415

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.APIConnectionError(request=request)
    backend = _build_backend(FakeMessages(raise_exc=exc), monkeypatch)

    result = backend.chat(
        messages=[{"role": "user", "content": "test"}],
        temperature=1.0,
        max_tokens=1000,
        cache_system=True,
        request_id="test-conn",
    )

    assert result.backend_meta["error"] == "connection_error"


def test_chat_echoes_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    final = FakeMessage(
        content=[FakeTextBlock(type="text", text="ok")],
        usage=FakeUsage(input_tokens=10, output_tokens=5),
    )
    backend = _build_backend(FakeMessages(final_message=final), monkeypatch)

    for rid in ["wake-w1-0001", "wake-w5-0042", "sub-tick-abc"]:
        result = backend.chat(
            messages=[{"role": "user", "content": "test"}],
            temperature=1.0,
            max_tokens=1000,
            cache_system=True,
            request_id=rid,
        )
        assert result.request_id == rid


def test_estimate_cost_usd_input_only() -> None:
    # 1000 input tokens @ $5/1M = $0.005
    cost = _estimate_cost_usd(
        input_tokens=1000,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    assert cost == pytest.approx(0.005)


def test_estimate_cost_usd_output_only() -> None:
    # 1000 output tokens @ $25/1M = $0.025
    cost = _estimate_cost_usd(
        input_tokens=0,
        output_tokens=1000,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    assert cost == pytest.approx(0.025)


def test_estimate_cost_usd_cache_read_discount() -> None:
    # Cache read is 10% of input price
    cost = _estimate_cost_usd(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=10000,
        cache_creation_input_tokens=0,
    )
    # 10000 * 5/1M * 0.1 = $0.005
    assert cost == pytest.approx(0.005)


def test_estimate_cost_usd_cache_write_premium() -> None:
    # Cache write is 125% of input price
    cost = _estimate_cost_usd(
        input_tokens=0,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=1000,
    )
    # 1000 * 5/1M * 1.25 = $0.00625
    assert cost == pytest.approx(0.00625)


def test_context_manager_closes_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, "sk-ant-test-key-xxxxxxxxxxxxxxxxxxxx")
    with AnthropicBackend(system="x") as backend:
        assert backend._client is not None
