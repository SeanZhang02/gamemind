"""Tests for perception/prompt_builder.py."""

from __future__ import annotations

import io

from PIL import Image

from gamemind.perception.prompt_builder import (
    build_tick_messages,
    build_tick_prompt,
    downsample_frame,
    parse_tick_response,
)


def _make_frame(w: int = 800, h: int = 600) -> bytes:
    img = Image.new("RGB", (w, h), (100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def test_build_tick_prompt_basic() -> None:
    prompt = build_tick_prompt(
        current_subgoal="find_tree",
        policy_hints=["if see log → attack"],
        available_actions={"forward": "W", "attack": "MouseLeft"},
        last_action="forward",
    )
    assert "find_tree" in prompt
    assert "- attack" in prompt
    assert "- forward" in prompt
    assert "EXACTLY ONE" in prompt


def test_prompt_truncates_hints() -> None:
    hints = [f"hint_{i}" for i in range(10)]
    prompt = build_tick_prompt(
        current_subgoal="test",
        policy_hints=hints,
        available_actions={"a": "A"},
        last_action="none",
    )
    assert "hint_0" in prompt
    assert "hint_2" in prompt
    assert "hint_3" not in prompt


def test_downsample_reduces_size() -> None:
    large = _make_frame(1920, 1080)
    small = downsample_frame(large)
    img = Image.open(io.BytesIO(small))
    assert img.width == 384
    assert img.height == 216
    assert len(small) < len(large)


def test_build_tick_messages_returns_system_and_messages() -> None:
    frame = _make_frame()
    system, messages = build_tick_messages(
        frame_bytes=frame,
        current_subgoal="chop",
        policy_hints=[],
        available_actions={"attack": "MouseLeft"},
        last_action="none",
    )
    assert "observe" in system.lower() or "analyze" in system.lower()
    assert len(messages) == 1
    assert "images" in messages[0]
    assert len(messages[0]["images"]) == 1


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


def test_parse_tick_response_empty_dict() -> None:
    result = parse_tick_response({})
    assert result["crosshair_block"] is None


def test_parse_tick_response_rejects_hallucinated_action() -> None:
    actions = {"forward": "W", "attack": "MouseLeft", "backward": "S"}
    result = parse_tick_response(
        {"action": "press_escape", "block": "stone"},
        available_actions=actions,
    )
    assert result["vlm_suggested_action"] is None  # rejected
    assert result["crosshair_block"] == "stone"  # block still parsed


def test_parse_tick_response_accepts_valid_action() -> None:
    actions = {"forward": "W", "attack": "MouseLeft"}
    result = parse_tick_response(
        {"action": "attack", "block": "oak_log"},
        available_actions=actions,
    )
    assert result["vlm_suggested_action"] == "attack"


def test_parse_tick_response_no_validation_without_actions() -> None:
    """Without available_actions, any action string passes through."""
    result = parse_tick_response({"action": "press_escape"})
    assert result["vlm_suggested_action"] == "press_escape"


def test_parse_tick_response_block_fallback_keys() -> None:
    """VLM might use 'crosshair_block' or 'crosshair' instead of 'block'."""
    result1 = parse_tick_response({"crosshair_block": "dirt"})
    assert result1["crosshair_block"] == "dirt"

    result2 = parse_tick_response({"crosshair": "sand"})
    assert result2["crosshair_block"] == "sand"
