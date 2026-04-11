"""Thin Ollama HTTP client for the Phase C-0 vision probe.

Uses /api/chat with format=json to force structured output, measures
wall-clock latency per call, and returns both the parsed response and raw
metadata for debugging.
"""

from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5vl:7b"


@dataclass
class InferenceResult:
    latency_ms: float
    raw_text: str
    parsed: dict[str, Any] | None
    json_parse_ok: bool
    total_duration_ns: int | None
    eval_count: int | None
    think_leaked: bool = False
    error: str | None = None


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def infer(
    image_path: Path,
    prompt: str,
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_HOST,
    timeout: float = 120.0,
) -> InferenceResult:
    # think=false is a defensive guard: if someone accidentally loads a
    # thinking-variant model, we tell Ollama to suppress CoT so format=json
    # still returns on time. Non-thinking models ignore the flag.
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [_encode_image(image_path)],
            }
        ],
        "format": "json",
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.0,
            "num_ctx": 4096,
        },
    }

    t0 = time.perf_counter()
    try:
        r = requests.post(f"{host}/api/chat", json=payload, timeout=timeout)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return InferenceResult(
            latency_ms=latency_ms,
            raw_text="",
            parsed=None,
            json_parse_ok=False,
            total_duration_ns=None,
            eval_count=None,
            error=f"http_error: {e}",
        )

    raw_text = data.get("message", {}).get("content", "")
    think_leaked = "<think>" in raw_text or "</think>" in raw_text
    parsed: dict[str, Any] | None = None
    json_ok = False
    try:
        parsed = json.loads(raw_text)
        json_ok = isinstance(parsed, dict)
    except (json.JSONDecodeError, TypeError):
        pass

    return InferenceResult(
        latency_ms=latency_ms,
        raw_text=raw_text,
        parsed=parsed,
        json_parse_ok=json_ok,
        total_duration_ns=data.get("total_duration"),
        eval_count=data.get("eval_count"),
        think_leaked=think_leaked,
    )


def _synthetic_image_b64(width: int, height: int) -> str:
    img = Image.new("RGB", (width, height), (127, 127, 127))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def warmup(model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> tuple[float, float]:
    """Two-phase warmup: text-only load, then a vision call to warm the image pipeline.

    Returns (text_warmup_ms, vision_warmup_ms).
    """
    t0 = time.perf_counter()
    r = requests.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 8},
        },
        timeout=300.0,
    )
    r.raise_for_status()
    text_ms = (time.perf_counter() - t0) * 1000.0

    # Vision warmup must match the SHAPE of the real probe calls (same prompt
    # style, same resolution, same options) to correctly warm the KV cache
    # prefill path and vision encoder. Otherwise the first real call still
    # pays ~2.5s of cold-start cost. We do two passes: first hits any lazy
    # init, second confirms steady-state.
    warmup_prompt = (
        "You are looking at a Minecraft first-person screenshot. Identify the "
        "block type directly in front of the player crosshair. Respond with ONLY "
        'valid JSON matching this schema: {"block": "<block_id>"}'
    )
    warmup_img = _synthetic_image_b64(1280, 720)

    vision_ms = 0.0
    for _ in range(2):
        t1 = time.perf_counter()
        r = requests.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": warmup_prompt,
                        "images": [warmup_img],
                    }
                ],
                "format": "json",
                "stream": False,
                "think": False,
                "options": {"temperature": 0.0, "num_ctx": 4096},
            },
            timeout=300.0,
        )
        r.raise_for_status()
        vision_ms = (time.perf_counter() - t1) * 1000.0
    return text_ms, vision_ms
