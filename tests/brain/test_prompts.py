"""Unit tests for prompt template loader + assembler."""

from __future__ import annotations

import pytest

from gamemind.brain.prompt_assembler import (
    BASE_SYSTEM_PROMPT,
    assemble_abort_evaluation,
    assemble_disagreement_arbiter,
    assemble_intent_decision,
    assemble_plan_decomposition,
    assemble_replan_from_stuck,
    assemble_task_completion_verification,
    to_messages,
)
from gamemind.brain.prompts import (
    TEMPLATE_DIR,
    TEMPLATE_NAMES,
    list_templates,
    render_template,
)


# ---------- loader tests ----------


def test_template_dir_exists() -> None:
    assert TEMPLATE_DIR.exists()
    assert TEMPLATE_DIR.is_dir()


def test_template_names_complete() -> None:
    assert TEMPLATE_NAMES == (
        "plan_decomposition",
        "replan_from_stuck",
        "abort_evaluation",
        "disagreement_arbiter",
        "task_completion_verification",
        "intent_decision",
    )


def test_all_templates_present() -> None:
    available = list_templates()
    assert set(available) == set(TEMPLATE_NAMES)
    assert len(available) == 6


def test_render_template_with_known_name() -> None:
    text = render_template(
        "plan_decomposition",
        display_name="Test Game",
        actions_bullet_list="- forward: W",
        world_facts="fact: value",
        task_description="do a thing",
        frame_summary="nothing visible",
        success_check="time_limit: 60s",
        abort_conditions="none",
    )
    assert "Test Game" in text
    assert "- forward: W" in text
    assert "do a thing" in text


def test_render_template_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown template"):
        render_template("not_a_template")


def test_render_template_safe_substitute_leaves_unknown_intact() -> None:
    """safe_substitute keeps unknown $placeholders instead of raising."""
    text = render_template(
        "plan_decomposition",
        display_name="X",
        # Intentionally omit most placeholders
    )
    # safe_substitute leaves unknown placeholders as-is
    assert "$actions_bullet_list" in text


def test_all_templates_mention_observation_tags() -> None:
    """Rule 4: every template must wrap untrusted content in observation tags."""
    for name in TEMPLATE_NAMES:
        text = (TEMPLATE_DIR / f"{name}.prompt").read_text(encoding="utf-8")
        assert "<observation>" in text, f"{name}: missing <observation> tag"
        assert "data" in text.lower() and "instruction" in text.lower(), (
            f"{name}: missing the 'data, not instructions' safety note"
        )


def test_no_template_contains_game_name_literal() -> None:
    """Rule 3: no game name in any prompt template — all game names come through adapter data."""
    forbidden = ("minecraft", "stardew", "factorio", "dead cells", "vampire survivors")
    for name in TEMPLATE_NAMES:
        text = (TEMPLATE_DIR / f"{name}.prompt").read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert word not in text, f"{name}: contains forbidden game name {word!r}"


# ---------- assembler tests ----------


def test_base_system_prompt_includes_safety_block() -> None:
    assert "observation" in BASE_SYSTEM_PROMPT
    assert "adapter-fact" in BASE_SYSTEM_PROMPT
    assert "DATA" in BASE_SYSTEM_PROMPT


def test_assemble_plan_decomposition() -> None:
    prompt = assemble_plan_decomposition(
        display_name="Minecraft Java",  # flows through as data, not template literal
        actions={"forward": "W", "attack": "MouseLeft"},
        world_facts={"trees": "produce logs"},
        task_description="chop 3 oak logs",
        frame_summary="I see a tree ahead",
        success_check="inventory_count(log) >= 3",
        abort_conditions="health < 0.3 OR time > 600s",
    )
    assert prompt.template_name == "plan_decomposition"
    assert "Minecraft Java" in prompt.user_content
    assert "- forward: W" in prompt.user_content
    assert "- attack: MouseLeft" in prompt.user_content
    assert "trees: produce logs" in prompt.user_content
    assert "chop 3 oak logs" in prompt.user_content
    assert prompt.system == BASE_SYSTEM_PROMPT


def test_assemble_plan_decomposition_sorts_actions_for_deterministic_cache() -> None:
    """Prompt caching requires deterministic ordering — actions must be sorted."""
    prompt1 = assemble_plan_decomposition(
        display_name="X",
        actions={"zulu": "Z", "alpha": "A", "mike": "M"},
        world_facts={},
        task_description="t",
        frame_summary="f",
        success_check="s",
        abort_conditions="a",
    )
    prompt2 = assemble_plan_decomposition(
        display_name="X",
        actions={"alpha": "A", "mike": "M", "zulu": "Z"},
        world_facts={},
        task_description="t",
        frame_summary="f",
        success_check="s",
        abort_conditions="a",
    )
    assert prompt1.user_content == prompt2.user_content


def test_assemble_plan_decomposition_empty_actions_renders_placeholder() -> None:
    prompt = assemble_plan_decomposition(
        display_name="X",
        actions={},
        world_facts={},
        task_description="t",
        frame_summary="f",
        success_check="s",
        abort_conditions="a",
    )
    assert "(no actions defined)" in prompt.user_content


def test_assemble_plan_decomposition_empty_world_facts() -> None:
    prompt = assemble_plan_decomposition(
        display_name="X",
        actions={"a": "A"},
        world_facts={},
        task_description="t",
        frame_summary="f",
        success_check="s",
        abort_conditions="a",
    )
    assert "(no world facts provided)" in prompt.user_content


def test_assemble_replan_from_stuck() -> None:
    prompt = assemble_replan_from_stuck(
        display_name="Test",
        world_facts={"k": "v"},
        frame_summary="stuck at wall",
        recent_actions="W W W W W",
        current_plan="walk forward to tree",
        stuck_seconds=22.5,
    )
    assert prompt.template_name == "replan_from_stuck"
    assert "22" in prompt.user_content
    assert "stuck at wall" in prompt.user_content
    assert "W W W W W" in prompt.user_content


def test_assemble_abort_evaluation() -> None:
    prompt = assemble_abort_evaluation(
        display_name="Test",
        world_facts={},
        frame_summary="low health",
        abort_trigger="health_threshold < 0.3 fired",
        current_goal="chop logs",
    )
    assert prompt.template_name == "abort_evaluation"
    assert "health_threshold < 0.3 fired" in prompt.user_content
    assert "chop logs" in prompt.user_content


def test_assemble_disagreement_arbiter() -> None:
    prompt = assemble_disagreement_arbiter(
        display_name="Test",
        world_facts={},
        frame_summary="current frame",
        recent_frames_summary="frame -2, -1, 0 all similar",
        critic_question="Is there a log in the player's inventory?",
    )
    assert prompt.template_name == "disagreement_arbiter"
    assert "Is there a log in the player's inventory?" in prompt.user_content


def test_assemble_task_completion_verification() -> None:
    prompt = assemble_task_completion_verification(
        display_name="Test",
        world_facts={},
        frame_summary="3 logs visible in hotbar",
        success_predicates="inventory_count(log) >= 3",
        task_description="chop 3 oak logs",
    )
    assert prompt.template_name == "task_completion_verification"
    assert "3 logs visible in hotbar" in prompt.user_content
    assert "inventory_count(log) >= 3" in prompt.user_content


def test_to_messages_returns_user_role_list() -> None:
    prompt = assemble_plan_decomposition(
        display_name="X",
        actions={"a": "A"},
        world_facts={},
        task_description="t",
        frame_summary="f",
        success_check="s",
        abort_conditions="a",
    )
    messages = to_messages(prompt)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == prompt.user_content


# ---------- intent_decision template tests ----------


def test_intent_decision_template_renders() -> None:
    """Template renders with all variables substituted."""
    text = render_template(
        "intent_decision",
        display_name="Test Game",
        world_facts="biome: plains",
        spatial_snapshot="oak_tree at (10, 65, -3), distance=5.2",
        current_subgoal="chop a tree",
        last_intents="approach oak_tree -> COMPLETED",
        available_intents="approach, look_around, attack_target, retreat",
        trigger_reason="COMPLETED: approach intent finished successfully",
    )
    assert "Test Game" in text
    assert "biome: plains" in text
    assert "oak_tree at (10, 65, -3)" in text
    assert "chop a tree" in text
    assert "approach oak_tree -> COMPLETED" in text
    assert "approach, look_around, attack_target, retreat" in text
    assert "COMPLETED: approach intent finished successfully" in text
    # No unsubstituted placeholders
    assert "$display_name" not in text
    assert "$spatial_snapshot" not in text
    assert "$current_subgoal" not in text
    assert "$last_intents" not in text
    assert "$available_intents" not in text
    assert "$trigger_reason" not in text
    assert "$world_facts" not in text


def test_intent_decision_template_no_game_name() -> None:
    """Rule 3: no hardcoded game names in the intent_decision template."""
    text = (TEMPLATE_DIR / "intent_decision.prompt").read_text(encoding="utf-8").lower()
    forbidden = ("minecraft", "stardew", "factorio", "dead cells", "vampire survivors")
    for word in forbidden:
        assert word not in text, f"intent_decision: contains forbidden game name {word!r}"


def test_intent_decision_template_has_observation_tags() -> None:
    """Rule 4: spatial_snapshot is wrapped in <observation> tags."""
    text = (TEMPLATE_DIR / "intent_decision.prompt").read_text(encoding="utf-8")
    assert "<observation>" in text
    assert "</observation>" in text
    assert "data" in text.lower() and "instruction" in text.lower(), (
        "intent_decision: missing the 'data, not instructions' safety note"
    )


def test_assemble_intent_decision_returns_assembled_prompt() -> None:
    """assemble_intent_decision returns correct AssembledPrompt structure."""
    prompt = assemble_intent_decision(
        display_name="Test Game",
        world_facts={"biome": "plains"},
        spatial_snapshot="oak_tree at (10, 65, -3), distance=5.2",
        current_subgoal="chop a tree",
        last_intents="approach oak_tree -> COMPLETED",
        available_intents="approach, look_around, attack_target, retreat",
        trigger_reason="COMPLETED: approach intent finished successfully",
    )
    assert prompt.template_name == "intent_decision"
    assert prompt.system == BASE_SYSTEM_PROMPT
    assert "Test Game" in prompt.user_content
    assert "oak_tree at (10, 65, -3)" in prompt.user_content
    assert "chop a tree" in prompt.user_content
    assert "approach oak_tree -> COMPLETED" in prompt.user_content
    assert "COMPLETED: approach intent finished successfully" in prompt.user_content


def test_assemble_intent_decision_wraps_world_facts() -> None:
    """world_facts are wrapped in <adapter-fact> tags (Design Rule 4)."""
    prompt = assemble_intent_decision(
        display_name="Test",
        world_facts={"trees": "produce logs", "stone": "needs pickaxe"},
        spatial_snapshot="snapshot data",
        current_subgoal="goal",
        last_intents="none",
        available_intents="approach, look_around",
        trigger_reason="STALLED: no progress",
    )
    assert "<adapter-fact>" in prompt.user_content
    assert "</adapter-fact>" in prompt.user_content
    assert "trees: produce logs" in prompt.user_content
    assert "stone: needs pickaxe" in prompt.user_content
