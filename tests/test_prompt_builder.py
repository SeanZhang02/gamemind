"""Tests for perception/prompt_builder.py."""

from __future__ import annotations

import io

from PIL import Image

from gamemind.perception.prompt_builder import (
    build_tick_messages,
    build_tick_prompt,
    downsample_frame,
    parse_spatial_response,
    parse_tick_response,
)


def _make_frame(w: int = 800, h: int = 600) -> bytes:
    img = Image.new("RGB", (w, h), (100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


# ── Prompt builder tests ──────────────────────────────────────────────


def test_build_spatial_prompt_no_action_field() -> None:
    """Action field and AVAILABLE ACTIONS section must not appear."""
    prompt = build_tick_prompt(
        current_subgoal="find_tree",
        policy_hints=["if see log -> attack"],
        last_action="forward",
    )
    # The JSON schema should not contain an "action" key
    assert '"action"' not in prompt
    assert "AVAILABLE ACTIONS" not in prompt
    assert "EXACTLY ONE" not in prompt


def test_build_spatial_prompt_includes_facing_query() -> None:
    prompt = build_tick_prompt(
        current_subgoal="observe",
        policy_hints=[],
        last_action="none",
    )
    assert "facing" in prompt
    assert "looking_down" in prompt or "looking_at_horizon" in prompt


def test_build_spatial_prompt_includes_anchors() -> None:
    prompt = build_tick_prompt(
        current_subgoal="observe",
        policy_hints=[],
        last_action="none",
    )
    assert "anchors" in prompt
    assert "direction" in prompt
    assert "distance" in prompt


def test_build_spatial_prompt_with_last_frame_diff() -> None:
    frame = _make_frame()
    system, messages = build_tick_messages(
        frame_bytes=frame,
        current_subgoal="observe",
        policy_hints=[],
        last_action="none",
        last_frame_diff="block changed from stone to dirt",
    )
    assert "Since last frame:" in messages[0]["content"]
    assert "block changed from stone to dirt" in messages[0]["content"]


def test_build_tick_messages_without_last_frame_diff() -> None:
    """Without last_frame_diff, no 'Since last frame' line appears."""
    frame = _make_frame()
    _, messages = build_tick_messages(
        frame_bytes=frame,
        current_subgoal="observe",
        policy_hints=[],
        last_action="none",
    )
    assert "Since last frame:" not in messages[0]["content"]


def test_prompt_contains_subgoal() -> None:
    prompt = build_tick_prompt(
        current_subgoal="find_tree",
        policy_hints=[],
        last_action="none",
    )
    assert "find_tree" in prompt


def test_build_tick_messages_returns_system_and_messages() -> None:
    frame = _make_frame()
    system, messages = build_tick_messages(
        frame_bytes=frame,
        current_subgoal="chop",
        policy_hints=[],
        last_action="none",
    )
    assert "json" in system.lower()
    assert len(messages) == 1
    assert "images" in messages[0]
    assert len(messages[0]["images"]) == 1


def test_build_tick_prompt_backward_compat_with_available_actions() -> None:
    """available_actions is accepted but ignored."""
    prompt = build_tick_prompt(
        current_subgoal="test",
        policy_hints=[],
        last_action="none",
        available_actions={"forward": "W", "attack": "MouseLeft"},
    )
    # Should not crash and should not contain action list
    assert "AVAILABLE ACTIONS" not in prompt


# ── parse_spatial_response tests ──────────────────────────────────────


def test_parse_spatial_response_valid_facing() -> None:
    result = parse_spatial_response(
        {
            "block": "oak_log",
            "facing": "looking_down",
            "spatial_context": "standing on grass",
            "anchors": [],
            "health": 0.9,
            "entities": [],
        }
    )
    assert result["player_facing"] == "looking_down"


def test_parse_spatial_response_invalid_facing_rejected() -> None:
    result = parse_spatial_response(
        {
            "block": "stone",
            "facing": "upside_down",
            "health": 0.5,
        }
    )
    assert result["player_facing"] is None
    assert result["crosshair_block"] == "stone"


def test_parse_spatial_response_valid_anchors() -> None:
    result = parse_spatial_response(
        {
            "block": "grass_block",
            "facing": "looking_at_horizon",
            "spatial_context": "open field",
            "anchors": [
                {"label": "oak_tree", "direction": "ahead", "distance": "medium"},
                {"label": "river", "direction": "left", "distance": "far"},
            ],
            "health": 1.0,
            "entities": ["cow"],
        }
    )
    assert result["anchors"] is not None
    assert len(result["anchors"]) == 2
    assert result["anchors"][0]["label"] == "oak_tree"
    assert result["anchors"][0]["direction"] == "ahead"
    assert result["anchors"][0]["distance"] == "medium"
    assert result["anchors"][1]["label"] == "river"


def test_parse_spatial_response_invalid_anchor_direction() -> None:
    result = parse_spatial_response(
        {
            "block": "stone",
            "facing": "looking_at_horizon",
            "anchors": [
                {"label": "tree", "direction": "northwest", "distance": "close"},
            ],
            "health": 0.8,
        }
    )
    # Invalid direction -> anchor rejected, list empty -> None
    assert result["anchors"] is None


def test_parse_spatial_response_missing_fields_graceful() -> None:
    result = parse_spatial_response({})
    assert result["crosshair_block"] is None
    assert result["player_facing"] is None
    assert result["spatial_context"] is None
    assert result["anchors"] is None
    assert result["health"] is None
    assert result["entities_nearby"] is None


def test_parse_spatial_response_none_input() -> None:
    result = parse_spatial_response(None)
    assert result["crosshair_block"] is None
    assert result["player_facing"] is None


def test_parse_spatial_response_block_fallback_keys() -> None:
    """VLM might use 'crosshair_block' or 'crosshair' instead of 'block'."""
    result1 = parse_spatial_response({"crosshair_block": "dirt"})
    assert result1["crosshair_block"] == "dirt"

    result2 = parse_spatial_response({"crosshair": "sand"})
    assert result2["crosshair_block"] == "sand"


def test_parse_spatial_response_health_normalization() -> None:
    """Health > 1.0 should be divided by 100."""
    result = parse_spatial_response({"health": 85})
    assert result["health"] == 0.85

    result2 = parse_spatial_response({"health": 0.5})
    assert result2["health"] == 0.5


# ── Downsample tests ──────────────────────────────────────────────────


def test_downsample_always_resizes() -> None:
    """Output should always be 512x288 regardless of input size."""
    large = _make_frame(1920, 1080)
    small = downsample_frame(large)
    img = Image.open(io.BytesIO(small))
    assert img.width == 512
    assert img.height == 288


def test_downsample_small_input_still_resizes() -> None:
    """Even small inputs get resized to 512x288."""
    tiny = _make_frame(200, 100)
    result = downsample_frame(tiny)
    img = Image.open(io.BytesIO(result))
    assert img.width == 512
    assert img.height == 288


# ── Legacy parse_tick_response tests (backward compat) ────────────────


def test_parse_tick_response_valid() -> None:
    result = parse_tick_response(
        {
            "block": "oak_log",
            "health": 0.8,
            "entities": ["zombie"],
            "action": "attack",
            "subgoal_ok": False,
            "reason": "log at crosshair",
        }
    )
    assert result["crosshair_block"] == "oak_log"
    assert result["health"] == 0.8
    assert result["vlm_suggested_action"] == "attack"


def test_parse_tick_response_none() -> None:
    result = parse_tick_response(None)
    assert result["crosshair_block"] is None
    assert result["vlm_suggested_action"] is None


def test_parse_tick_response_block_fallback_keys() -> None:
    result1 = parse_tick_response({"crosshair_block": "dirt"})
    assert result1["crosshair_block"] == "dirt"

    result2 = parse_tick_response({"crosshair": "sand"})
    assert result2["crosshair_block"] == "sand"
