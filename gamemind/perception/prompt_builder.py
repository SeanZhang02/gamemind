"""Per-tick VLM prompt builder — perception + action suggestion in one call.

Builds the Ollama prompt that runs every ~1100ms. Injects:
  - current_subgoal (from Planner via Blackboard)
  - policy_hints (from Claude W1/W2, max 3, max 40 tokens)
  - available_actions (from adapter YAML, CSV format)
  - last_action (from Blackboard)

Output schema drives Blackboard writes: crosshair_block, entities_nearby,
health, action suggestion, subgoal assessment.

Prompt kept short (<300 input tokens) to minimize inference latency.
Image downsampled to 640x360 before encoding.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

_SYSTEM_PROMPT = (
    "You observe a game screenshot each tick. Analyze what you see and choose the best action.\n"
    "\n"
    "RULES:\n"
    "- Respond with ONLY valid JSON, no other text\n"
    '- The "action" field MUST be exactly one value from the available actions list. '
    "No other values are allowed.\n"
    '- The "block" field should be the block type the crosshair is pointing at '
    '(e.g. "oak_log", "stone", "air"), or null if unclear\n'
    "- Be specific about block types — use Minecraft block IDs when possible\n"
    "- If your recent actions show the same action repeating without progress "
    "(same block, no change), choose a DIFFERENT action to break the loop"
)

_TICK_TEMPLATE = (
    "Current subgoal: $subgoal\n"
    "Last action: $last_action\n"
    "Recent actions: $recent_actions\n"
    "Hints: $hints\n"
    "\n"
    "AVAILABLE ACTIONS (choose EXACTLY ONE):\n"
    "$actions_list\n"
    "\n"
    'Respond with JSON: {"block": "<block_at_crosshair>", "action": "<one_from_list_above>", '
    '"health": <0.0-1.0>, "entities": [...], "subgoal_ok": <bool>, "reason": "<why>"}'
)

_TARGET_WIDTH = 384
_TARGET_HEIGHT = 216


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
    recent_actions: list[tuple[str, str | None]] | None = None,
) -> str:
    """Build the per-tick VLM prompt text.

    Uses bulleted action list (one per line) so VLM can clearly see options.
    Max 3 policy hints, truncated to 80 chars each.
    """
    actions_list = "\n".join(f"- {a}" for a in sorted(available_actions.keys()))

    truncated_hints = policy_hints[:3]
    hints_text = "; ".join(h[:80] for h in truncated_hints) if truncated_hints else "none"

    if recent_actions:
        recent_text = ", ".join(f"{act}\u2192{blk}" if blk else act for act, blk in recent_actions)
    else:
        recent_text = "(none)"

    return (
        _TICK_TEMPLATE.replace("$subgoal", current_subgoal or "observe")
        .replace("$hints", hints_text)
        .replace("$actions_list", actions_list)
        .replace("$last_action", last_action or "none")
        .replace("$recent_actions", recent_text)
    )


def build_tick_messages(
    *,
    frame_bytes: bytes,
    current_subgoal: str,
    policy_hints: list[str],
    available_actions: dict[str, str],
    last_action: str,
    recent_actions: list[tuple[str, str | None]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build complete Ollama chat call arguments.

    Returns (system_prompt, messages) ready for OllamaBackend.chat().
    """
    prompt_text = build_tick_prompt(
        current_subgoal=current_subgoal,
        policy_hints=policy_hints,
        available_actions=available_actions,
        last_action=last_action,
        recent_actions=recent_actions,
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


def parse_tick_response(
    parsed_json: dict[str, Any] | None,
    *,
    available_actions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract Blackboard-ready fields from VLM response JSON.

    Returns dict with standardized keys. Missing fields get None.

    If *available_actions* is provided, the ``action`` field is validated
    against its keys. Hallucinated action names are rejected (set to None).
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

    # Block field: try multiple key names the VLM might use
    block = (
        parsed_json.get("block")
        or parsed_json.get("crosshair_block")
        or parsed_json.get("crosshair")
    )

    # Action field: validate against available actions to reject hallucinations
    action = parsed_json.get("action")
    if action and isinstance(action, str):
        action = action.strip()
        # Case-insensitive match: VLM might return "Forward" instead of "forward"
        if available_actions:
            action_lower = action.lower()
            matched = next((a for a in available_actions if a.lower() == action_lower), None)
            action = matched  # None if no case-insensitive match

    return {
        "crosshair_block": block,
        "health": health_raw,
        "entities_nearby": parsed_json.get("entities"),
        "vlm_suggested_action": action,
        "subgoal_ok": parsed_json.get("subgoal_ok"),
        "action_reason": parsed_json.get("reason"),
    }
