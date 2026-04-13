"""OllamaBackend — first `LLMBackend` implementor (Amendment A12).

Refactored from `phase-c-0/probe/client.py` (locked by C0_CLOSEOUT) into
the Phase C package. Key differences from the probe:

- Generic warmup prompt (Design Rule 3 compliance — no "Minecraft"
  literal, since the module now lives under `gamemind/` which is scoped
  by the Rule 3 CI linter).
- Implements `LLMBackend` Protocol with `LLMResponse`.
- `num_ctx` is a config field per Amendment A15 instead of a constant.
- Connection pooling via a module-level `requests.Session` to avoid
  per-tick socket setup cost.
- `chat()` returns `LLMResponse.text = ""` and `parsed_json = None` on
  any backend error; never raises from network failures. Raises only on
  caller errors (bad arguments).

Phase C Step 1 iter-2 scope: just the backend. The perception daemon
loop that wraps it lives in a later iteration.
"""

from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from gamemind.brain.backend import LLMResponse

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:8b-instruct-q4_K_M"
DEFAULT_NUM_CTX = 4096
DEFAULT_TIMEOUT_S = 120.0


def _encode_image_bytes(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


def _encode_image_path(path: Path) -> str:
    return _encode_image_bytes(path.read_bytes())


class OllamaBackend:
    """Ollama HTTP client implementing `LLMBackend` for Layer 1 perception.

    Usage:
        backend = OllamaBackend()
        backend.warmup()
        result = backend.chat(
            messages=[{"role": "user", "content": "...", "images": [b64]}],
            temperature=0.0,
            max_tokens=512,
            cache_system=False,  # Ollama ignores this
            request_id="tick-0001",
        )
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        model: str = DEFAULT_MODEL,
        num_ctx: int = DEFAULT_NUM_CTX,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        explicit_long_context: bool = False,
    ) -> None:
        """Construct an OllamaBackend.

        Amendment A15: `num_ctx` values > 8192 require
        `explicit_long_context=True` to guard against typo-induced
        memory blowups.
        """
        if num_ctx > 8192 and not explicit_long_context:
            raise ValueError(
                f"num_ctx={num_ctx} > 8192 requires explicit_long_context=True "
                "(Amendment A15 guard against accidental doubling of model memory)"
            )
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.timeout_s = timeout_s
        # httpx.Client reuses the underlying connection pool across calls,
        # eliminating per-tick TCP setup. Used for BOTH chat and warmup.
        self._client = httpx.Client(timeout=timeout_s)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        cache_system: bool,  # noqa: ARG002 — Ollama ignores this; kept for Protocol compat
        request_id: str,
        emit_event: bool = True,  # noqa: ARG002 — events writer hookup lands in iter-4
    ) -> LLMResponse:
        """Call Ollama /api/chat with format=json and temp=0 for determinism.

        Returns LLMResponse. On backend error, returns LLMResponse with
        text="", parsed_json=None, and backend_meta["error"] set.
        Never raises from network or model errors — the caller routes
        recovery via Amendment A6 backend absence policies.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            # think=false: defensive guard against accidentally loading a
            # thinking-variant model (those bust p90 latency per C-0).
            "think": False,
            "options": {
                "temperature": temperature,
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens,
            },
        }
        t0 = time.perf_counter()
        try:
            response = self._client.post(f"{self.host}/api/chat", json=payload)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return LLMResponse(
                text="",
                parsed_json=None,
                prompt_tokens=0,
                completion_tokens=0,
                cost_estimate_usd=0.0,
                latency_ms=latency_ms,
                request_id=request_id,
                cached_system=False,
                backend_meta={
                    "error": type(exc).__name__,
                    "error_msg": str(exc),
                    "backend": "ollama",
                },
            )

        raw_text = data.get("message", {}).get("content", "")
        think_leaked = "<think>" in raw_text or "</think>" in raw_text
        parsed: dict[str, Any] | None = None
        stripped = raw_text.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        try:
            loaded = json.loads(stripped)
            if isinstance(loaded, dict):
                parsed = loaded
        except (json.JSONDecodeError, TypeError):
            pass

        return LLMResponse(
            text=raw_text,
            parsed_json=parsed,
            prompt_tokens=data.get("prompt_eval_count", 0) or 0,
            completion_tokens=data.get("eval_count", 0) or 0,
            cost_estimate_usd=0.0,  # local model, no API cost
            latency_ms=latency_ms,
            request_id=request_id,
            cached_system=False,
            backend_meta={
                "backend": "ollama",
                "total_duration_ns": data.get("total_duration"),
                "eval_count": data.get("eval_count"),
                "prompt_eval_count": data.get("prompt_eval_count"),
                "think_leaked": think_leaked,
            },
        )

    def warmup(self) -> tuple[float, float]:
        """Two-phase warmup: text-only load, then a vision call.

        Returns `(text_warmup_ms, vision_warmup_ms)`.

        The vision warmup matches the shape of real perception calls
        (same prompt style, same resolution, same options) to correctly
        warm the KV cache prefill path and vision encoder. Without this,
        the first real inference pays ~2.5s cold-start cost.

        The prompt is deliberately game-generic (Design Rule 3):
        asks for a trivial JSON with no game-specific terminology.
        """
        # Phase 1: text-only
        t0 = time.perf_counter()
        self._client.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 8},
            },
            timeout=300.0,
        ).raise_for_status()
        text_ms = (time.perf_counter() - t0) * 1000.0

        # Phase 2: vision call on a neutral grey image to warm the encoder
        # + KV cache prefill path. Prompt is game-generic: asks for a
        # trivial JSON field to exercise format=json on the hot path.
        grey_image = Image.new("RGB", (1280, 720), (127, 127, 127))
        buf = io.BytesIO()
        grey_image.save(buf, format="PNG")
        warmup_b64 = _encode_image_bytes(buf.getvalue())
        warmup_prompt = (
            "You are a vision model. Look at this image and respond with ONLY "
            'valid JSON matching this schema: {"ok": true}'
        )

        vision_ms = 0.0
        for _ in range(2):  # two passes confirm steady-state
            t1 = time.perf_counter()
            self._client.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": warmup_prompt,
                            "images": [warmup_b64],
                        }
                    ],
                    "format": "json",
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.0, "num_ctx": self.num_ctx},
                },
                timeout=300.0,
            ).raise_for_status()
            vision_ms = (time.perf_counter() - t1) * 1000.0
        return text_ms, vision_ms

    def close(self) -> None:
        """Close the underlying httpx.Client. Safe to call multiple times."""
        self._client.close()

    def __enter__(self) -> OllamaBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
