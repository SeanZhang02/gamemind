"""Unit tests for adapter pydantic schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gamemind.adapter.schema import (
    CURRENT_SCHEMA_VERSION,
    AbortCondition,
    Adapter,
    GoalGrammar,
    PerceptionConfig,
    Predicate,
    SuccessCheck,
)


def _minimal_adapter_data() -> dict:
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "display_name": "Test Game",
        "actions": {"forward": "W"},
        "goal_grammars": {
            "test_goal": {
                "description": "A test goal",
                "preconditions": [],
                "success_check": {
                    "predicate": {
                        "type": "inventory_count",
                        "target": "log",
                        "operator": ">=",
                        "value": 3,
                    },
                },
                "abort_conditions": [],
            }
        },
    }


def test_current_schema_version_is_1() -> None:
    assert CURRENT_SCHEMA_VERSION == 1


def test_minimal_adapter_loads() -> None:
    adapter = Adapter.model_validate(_minimal_adapter_data())
    assert adapter.schema_version == 1
    assert adapter.display_name == "Test Game"
    assert adapter.actions == {"forward": "W"}
    assert "test_goal" in adapter.goal_grammars
    # Defaults
    assert adapter.world_facts == {}
    assert adapter.inventory_ui == {}
    assert adapter.perception.freshness_budget_ms == 750.0
    assert adapter.perception.tick_hz == 2.0


def test_adapter_is_frozen() -> None:
    adapter = Adapter.model_validate(_minimal_adapter_data())
    with pytest.raises(ValidationError):
        adapter.display_name = "Mutated"  # frozen model rejects assignment


def test_adapter_rejects_unknown_top_level_key() -> None:
    data = _minimal_adapter_data()
    data["mystery_key"] = "not allowed"
    with pytest.raises(ValidationError, match="mystery_key"):
        Adapter.model_validate(data)


def test_adapter_rejects_unknown_nested_key() -> None:
    data = _minimal_adapter_data()
    data["perception"] = {"freshness_budget_ms": 500.0, "rogue_key": 1}
    with pytest.raises(ValidationError, match="rogue_key"):
        Adapter.model_validate(data)


def test_adapter_rejects_wrong_schema_version() -> None:
    data = _minimal_adapter_data()
    data["schema_version"] = 99
    with pytest.raises(ValidationError, match="schema_version"):
        Adapter.model_validate(data)


def test_adapter_rejects_empty_actions() -> None:
    data = _minimal_adapter_data()
    data["actions"] = {}
    with pytest.raises(ValidationError, match="actions"):
        Adapter.model_validate(data)


def test_adapter_rejects_empty_goal_grammars() -> None:
    data = _minimal_adapter_data()
    data["goal_grammars"] = {}
    with pytest.raises(ValidationError, match="goal_grammars"):
        Adapter.model_validate(data)


def test_success_check_requires_exactly_one_of() -> None:
    # Predicate only: OK
    SuccessCheck.model_validate({"predicate": {"type": "time_limit", "seconds": 10.0}})
    # any_of: OK
    SuccessCheck.model_validate(
        {
            "any_of": [
                {"predicate": {"type": "time_limit", "seconds": 10.0}},
            ]
        }
    )
    # Both predicate AND any_of: NOT OK
    with pytest.raises(ValueError, match="exactly one"):
        SuccessCheck.model_validate(
            {
                "predicate": {"type": "time_limit", "seconds": 10.0},
                "any_of": [
                    {"predicate": {"type": "time_limit", "seconds": 10.0}},
                ],
            }
        )
    # Neither: NOT OK
    with pytest.raises(ValueError, match="exactly one"):
        SuccessCheck.model_validate({})


def test_success_check_rejects_empty_any_of() -> None:
    with pytest.raises(ValueError, match="at least one"):
        SuccessCheck.model_validate({"any_of": []})


def test_success_check_nested_any_all() -> None:
    """any_of containing all_of containing a predicate — valid nesting."""
    SuccessCheck.model_validate(
        {
            "any_of": [
                {
                    "all_of": [
                        {"predicate": {"type": "time_limit", "seconds": 10.0}},
                        {"predicate": {"type": "stuck_detector"}},
                    ]
                },
                {"predicate": {"type": "time_limit", "seconds": 30.0}},
            ]
        }
    )


def test_predicate_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError, match="type"):
        Predicate.model_validate({"type": "nuke_the_world"})


def test_predicate_strict_rejects_extra_key() -> None:
    with pytest.raises(ValidationError, match="mystery_field"):
        Predicate.model_validate({"type": "inventory_count", "target": "log", "mystery_field": "x"})


def test_abort_condition_strict() -> None:
    AbortCondition.model_validate({"type": "health_threshold", "operator": "<", "value": 0.3})
    with pytest.raises(ValidationError):
        AbortCondition.model_validate({"type": "health_threshold", "unknown": "x"})


def test_perception_config_defaults() -> None:
    cfg = PerceptionConfig.model_validate({})
    assert cfg.freshness_budget_ms == 750.0
    assert cfg.tick_hz == 2.0


def test_goal_grammar_shape() -> None:
    gg = GoalGrammar.model_validate(
        {
            "description": "x",
            "preconditions": ["a"],
            "success_check": {"predicate": {"type": "time_limit", "seconds": 10.0}},
            "abort_conditions": [
                {"type": "time_limit", "seconds": 100.0},
            ],
        }
    )
    assert gg.description == "x"
    assert len(gg.abort_conditions) == 1
