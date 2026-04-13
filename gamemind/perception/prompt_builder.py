"""Per-tick VLM prompt builder — perception + action suggestion in one call.

Builds the Ollama prompt that runs every ~1100ms. Injects:
  - current_subgoal (from Planner via Blackboard)
  - policy_hints (from Claude W1/W2, max 3, max 40 tokens)
  - available_actions (from adapter YAML, CSV format)
  - last_action (from Blackboard)

Output schema drives Blackboard writes: crosshair_block, entities_nearby,
health, action suggestion, subgoal assessment.

Prompt kept short (<200 input tokens) to minimize inference latency.
Image downsampled to 640x360 before encoding.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

_SYSTEM_PROMPT = (
    "You observe a game screenshot each tick. Report what you see and suggest one action. "
    "Respond with ONLY valid JSON. No prose."
)

_TICK_TEMPLATE = (
    "Goal: $subgoal\n"
    "Hints: $hints\n"
    "Actions: $actions\n"
    "Last action: $last_action\n"
    "Report JSON: {block, health, entities, action, subgoal_ok, reason}"
)

_TARGET_WIDTH = 640
_TARGET_HEIGHT = 360


def downsample_frame(frame_bytes: bytes) -> bytes:
    """Downsample captured frame to 640x360 WEBP for VLM input."""
    img = Image.open(io.BytesIO(frame_bytes))
    if img.width > _TARGET_WIDTH or img.height > _TARGET_HEIGHT:
        img = img.resize((_TARGET_WIDTH, _TARGET_HEIGHT), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


def encode_frame_b64(frame_bytes: bytes) -> str:
    """Downsample + base64 encode a frame for Ollama API."""
    small = downsample_frame(frame_bytes)
    return base64.b64encode(small).decode("ascii")


def build_tick_prompt(
    *,
    current_subgoal: str,
    policy_hints: list[str],
    available_actions: dict[str, str],
    last_action: str,
) -> str:
    """Build the per-tick VLM prompt text.

    Keeps total prompt under ~200 tokens by:
      - CSV action list (not bullet points)
      - Max 3 policy hints, truncated to 40 tokens each
      - Single-line goal
    """
    actions_csv = ",".join(sorted(available_actions.keys()))

    truncated_hints = policy_hints[:3]
    hints_text = "; ".join(h[:80] for h in truncated_hints) if truncated_hints else "none"

    return (
        _TICK_TEMPLATE.replace("$subgoal", current_subgoal or "observe")
        .replace("$hints", hints_text)
        .replace("$actions", actions_csv)
        .replace("$last_action", last_action or "none")
    )


def build_tick_messages(
    *,
    frame_bytes: bytes,
    current_subgoal: str,
    policy_hints: list[str],
    available_actions: dict[str, str],
    last_action: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Build complete Ollama chat call arguments.

    Returns (system_prompt, messages) ready for OllamaBackend.chat().
    """
    prompt_text = build_tick_prompt(
        current_subgoal=current_subgoal,
        policy_hints=policy_hints,
        available_actions=available_actions,
        last_action=last_action,
    )
    img_b64 = encode_frame_b64(frame_bytes)
    messages = [
        {
            "role": "user",
            "content": prompt_text,
            "images": [img_b64],
        }
    ]
    return _SYSTEM_PROMPT, messages


def parse_tick_response(parsed_json: dict[str, Any] | None) -> dict[str, Any]:
    """Extract Blackboard-ready fields from VLM response JSON.

    Returns dict with standardized keys. Missing fields get None.
    """
    if not parsed_json or not isinstance(parsed_json, dict):
        return {
            "crosshair_block": None,
            "health": None,
            "entities_nearby": None,
            "vlm_suggested_action": None,
            "subgoal_ok": None,
            "action_reason": None,
        }
    health_raw = parsed_json.get("health")
    if health_raw is not None:
        try:
            health_val = float(health_raw)
            if health_val > 1.0:
                health_val = health_val / 100.0
            health_raw = max(0.0, min(1.0, health_val))
        except (ValueError, TypeError):
            health_raw = None

    return {
        "crosshair_block": parsed_json.get("block"),
        "health": health_raw,
        "entities_nearby": parsed_json.get("entities"),
        "vlm_suggested_action": parsed_json.get("action"),
        "subgoal_ok": parsed_json.get("subgoal_ok"),
        "action_reason": parsed_json.get("reason"),
    }
