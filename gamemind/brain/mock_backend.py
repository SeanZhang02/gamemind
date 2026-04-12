"""MockBrainBackend — scripted LLMBackend for dry-run and integration tests.

Returns pre-configured LLMResponse objects in FIFO order. Raises
IndexError if more calls are made than scripted responses exist. Thread-
safe — the runner may call from different threads during warm-up and
main loop.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from gamemind.brain.backend import LLMResponse


class MockBrainBackend:
    """Scripted LLMBackend satisfying the Protocol.

    Usage:
        mock = MockBrainBackend(scripted=[
            LLMResponse(text='{"plan": [...]}', parsed_json={"plan": [...]},
                        prompt_tokens=100, completion_tokens=50,
                        cost_estimate_usd=0.0, latency_ms=0.0,
                        request_id="", cached_system=False),
            ...
        ])
        response = mock.chat(messages, temperature=0.0, max_tokens=1024,
                             cache_system=False, request_id="r1")
    """

    def __init__(self, scripted: list[LLMResponse] | None = None) -> None:
        self._responses: deque[LLMResponse] = deque(scripted or [])
        self._lock = threading.Lock()
        self._call_count = 0
        self._calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        cache_system: bool,
        request_id: str,
        emit_event: bool = True,
    ) -> LLMResponse:
        t0 = time.perf_counter()
        with self._lock:
            self._call_count += 1
            self._calls.append(
                {
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "request_id": request_id,
                    "call_index": self._call_count,
                }
            )
            if not self._responses:
                latency = (time.perf_counter() - t0) * 1000
                return LLMResponse(
                    text="",
                    parsed_json=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    cost_estimate_usd=0.0,
                    latency_ms=latency,
                    request_id=request_id,
                    cached_system=False,
                    backend_meta={"error": "mock_exhausted"},
                )
            resp = self._responses.popleft()
        resp.request_id = request_id
        resp.latency_ms = (time.perf_counter() - t0) * 1000
        return resp

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_count

    @property
    def calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._calls)
