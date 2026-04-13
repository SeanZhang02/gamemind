"""Per-tick VLM prompt builder — spatial perception layer.

Builds the Ollama prompt that runs every ~1100ms. Injects:
  - current_subgoal (from Planner via Blackboard)
  - policy_hints (from Claude W1/W2, max 3, max 40 tokens)
  - last_action (from Blackboard)
  - last_frame_diff (optional, change-since-last context)

Output schema drives Blackboard writes: crosshair_block, player_facing,
spatial_context, anchors, entities_nearby, health.

Prompt kept short (<300 input tokens) to minimize inference latency.
Image downsampled to 512x288 before encoding.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image

_SYSTEM_PROMPT = (
    "You observe a game screenshot each tick. Describe what you see in structured JSON.\n"
    "\n"
    "RULES:\n"
    "- Respond with ONLY valid JSON, no other text\n"
    '- The "block" field should be the block type the crosshair is pointing at '
    '(e.g. "oak_log", "stone", "air"), or null if unclear\n'
    "- Be specific about block types -- use the game's native block/object names when possible\n"
    '- The "facing" field describes your camera orientation: '
    '"looking_down", "looking_at_horizon", or "looking_up"\n'
    '- The "anchors" field lists notable objects with their relative direction and distance'
)

_TICK_TEMPLATE = (
    "Current subgoal: $subgoal\n"
    "Last action: $last_action\n"
    "Recent actions: $recent_actions\n"
    "Hints: $hints\n"
    "\n"
    "Respond with JSON:\n"
    '{"block": "<block_at_crosshair>", '
    '"facing": "<looking_down | looking_at_horizon | looking_up>", '
    '"spatial_context": "<one sentence describing surroundings>", '
    '"anchors": [{"label": "<thing>", "direction": "<ahead|left|right|behind|ahead_left|ahead_right>", '
    '"distance": "<close|medium|far>"}], '
    '"health": 0.0-1.0, '
    '"entities": ["<entity_name>"]}'
)

_TARGET_WIDTH = 512
_TARGET_HEIGHT = 288

_VALID_FACINGS = frozenset({"looking_down", "looking_at_horizon", "looking_up"})
_VALID_DIRECTIONS = frozenset(
    {"ahead", "left", "right", "behind", "ahead_left", "ahead_right"}
)
_VALID_DISTANCES = frozenset({"close", "medium", "far"})


def downsample_frame(frame_bytes: bytes) -> bytes:
    """Downsample captured frame to 512x288 WEBP for VLM input."""
    img = Image.open(io.BytesIO(frame_bytes))
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
    last_action: str,
    recent_actions: list[tuple[str, str | None]] | None = None,
    available_actions: dict[str, str] | None = None,
) -> str:
    """Build the per-tick VLM prompt text.

    Max 3 policy hints, truncated to 80 chars each.

    The ``available_actions`` parameter is accepted for backward compatibility
    but is ignored (actions are no longer part of the VLM output schema).
    """
    truncated_hints = policy_hints[:3]
    hints_text = "; ".join(h[:80] for h in truncated_hints) if truncated_hints else "none"

    if recent_actions:
        recent_text = ", ".join(f"{act}\u2192{blk}" if blk else act for act, blk in recent_actions)
    else:
        recent_text = "(none)"

    return (
        _TICK_TEMPLATE.replace("$subgoal", current_subgoal or "observe")
        .replace("$hints", hints_text)
        .replace("$last_action", last_action or "none")
        .replace("$recent_actions", recent_text)
    )


def build_tick_messages(
    *,
    frame_bytes: bytes,
    current_subgoal: str,
    policy_hints: list[str],
    last_action: str,
    recent_actions: list[tuple[str, str | None]] | None = None,
    available_actions: dict[str, str] | None = None,
    last_frame_diff: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build complete Ollama chat call arguments.

    Returns (system_prompt, messages) ready for OllamaBackend.chat().

    The ``available_actions`` parameter is accepted for backward compatibility
    but is ignored (actions are no longer part of the VLM output schema).

    If ``last_frame_diff`` is provided, it is prepended to the user message
    as change-since-last context.
    """
    prompt_text = build_tick_prompt(
        current_subgoal=current_subgoal,
        policy_hints=policy_hints,
        last_action=last_action,
        recent_actions=recent_actions,
    )
    if last_frame_diff:
        prompt_text = f"Since last frame: {last_frame_diff}\n{prompt_text}"

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
    """Extract Blackboard-ready fields from VLM response JSON (legacy).

    Kept for backward compatibility. New code should use
    ``parse_spatial_response()``.
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


def parse_spatial_response(
    parsed_json: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract spatial perception fields from VLM response JSON.

    Returns dict with standardized keys. Missing/invalid fields get None.
    Validates ``facing`` against allowed categories and ``anchors``
    entries against direction/distance enums.
    """
    if not parsed_json or not isinstance(parsed_json, dict):
        return {
            "crosshair_block": None,
            "player_facing": None,
            "spatial_context": None,
            "anchors": None,
            "health": None,
            "entities_nearby": None,
        }

    # Health normalization (same logic as legacy)
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

    # Facing field: validate against allowed categories
    facing = parsed_json.get("facing")
    if facing and isinstance(facing, str):
        facing = facing.strip()
        if facing not in _VALID_FACINGS:
            facing = None
    else:
        facing = None

    # Spatial context
    spatial_context = parsed_json.get("spatial_context")
    if spatial_context and isinstance(spatial_context, str):
        spatial_context = spatial_context.strip()
    else:
        spatial_context = None

    # Anchors: validate each entry
    anchors_raw = parsed_json.get("anchors")
    anchors = None
    if anchors_raw and isinstance(anchors_raw, list):
        validated = []
        for item in anchors_raw:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            direction = item.get("direction")
            distance = item.get("distance")
            if (
                label
                and isinstance(label, str)
                and isinstance(direction, str)
                and isinstance(distance, str)
                and direction in _VALID_DIRECTIONS
                and distance in _VALID_DISTANCES
            ):
                validated.append(
                    {"label": label, "direction": direction, "distance": distance}
                )
        if validated:
            anchors = validated

    return {
        "crosshair_block": block,
        "player_facing": facing,
        "spatial_context": spatial_context,
        "anchors": anchors,
        "health": health_raw,
        "entities_nearby": parsed_json.get("entities"),
    }
