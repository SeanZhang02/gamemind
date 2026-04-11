"""Adapter pydantic schema — frozen contract for `adapters/*.yaml`.

Per §2 OQ-4 of docs/final-design.md + Amendment A1 (schema_version) +
Amendment A8 (drop py-code rejector, keep yaml.safe_load + strict
pydantic). Unknown keys at ANY level raise ValidationError — this is
the single most important guard against silent schema drift.

Schema v1 covers the fields Phase C Step 3 needs:
  - actions: named key/button bindings (data, not Python)
  - inventory_ui: HUD geometry hints (pure data)
  - goal_grammars: task templates with precondition + success_check +
    abort_conditions
  - world_facts: static per-game facts exposed to brain prompts
  - perception: freshness budget override (Amendment A1)

Future schema bumps (v2+) can extend with new predicate types or new
layers; the loader dispatches by schema_version so v1 adapters keep
loading unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CURRENT_SCHEMA_VERSION: int = 1


class _StrictModel(BaseModel):
    """Base pydantic model with strict = unknown keys rejected.

    Per Amendment A8, we rely on pydantic strict validation INSTEAD of
    the v2.4 walk-and-reject-python-strings heuristic. Unknown keys at
    any nesting level raise ValidationError.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class Predicate(_StrictModel):
    """A single verify predicate — tier 1/2/3 per §2 OQ-4 checker tiers."""

    type: Literal[
        "inventory_count",
        "template_match",
        "vision_critic",
        "health_threshold",
        "time_limit",
        "stuck_detector",
    ]
    # Tier-specific fields. All optional; the verify engine picks the
    # relevant ones per `type`. Extra keys still forbidden (strict mode)
    # so typos fail at load time.
    target: str | None = None
    operator: Literal[">=", ">", "==", "<=", "<"] | None = None
    value: int | float | bool | None = None
    question: str | None = None
    seconds: float | None = None
    template: str | None = None


class SuccessCheck(_StrictModel):
    """Composition of predicates: any_of / all_of / a single predicate.

    Exactly one of (any_of, all_of, predicate) must be set. Nesting is
    supported: an any_of may contain SuccessCheck nodes for all_of
    sub-groups and vice versa.
    """

    any_of: list[SuccessCheck] | None = None
    all_of: list[SuccessCheck] | None = None
    predicate: Predicate | None = None

    @field_validator("any_of", "all_of", mode="after")
    @classmethod
    def _non_empty(cls, v: list[SuccessCheck] | None) -> list[SuccessCheck] | None:
        if v is not None and len(v) == 0:
            raise ValueError("any_of / all_of must contain at least one SuccessCheck")
        return v

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        set_count = sum(1 for x in (self.any_of, self.all_of, self.predicate) if x is not None)
        if set_count != 1:
            raise ValueError(
                f"SuccessCheck must set exactly one of: any_of, all_of, predicate (got {set_count})"
            )


class AbortCondition(_StrictModel):
    """A single abort predicate — triggers session abort when true.

    Abort conditions evaluate to a predicate that, when fires, causes
    Layer 2 to emit Trigger W3 (§1.4) and terminate the session.
    """

    type: Literal["health_threshold", "time_limit", "stuck_detector", "vision_critic"]
    operator: Literal[">=", ">", "==", "<=", "<"] | None = None
    value: int | float | bool | None = None
    seconds: float | None = None
    question: str | None = None


class GoalGrammar(_StrictModel):
    """A named goal template — `chop_logs`, `water_crops`, etc.

    Brain prompt templates query adapter.goal_grammars[goal_name] at
    runtime to assemble task-specific context without hardcoding game
    names (Rule 3).
    """

    description: str
    preconditions: list[str] = Field(default_factory=list)
    success_check: SuccessCheck
    abort_conditions: list[AbortCondition] = Field(default_factory=list)


class PerceptionConfig(_StrictModel):
    """Per-adapter perception knobs.

    Amendment A1: freshness_budget_ms can be overridden per game if a
    game's tolerance differs from the 750ms default (e.g. slow turn-
    based games can afford more, twitch action games may want less).
    """

    freshness_budget_ms: float = 750.0
    tick_hz: float = 2.0


class Adapter(_StrictModel):
    """Top-level adapter — the full contract for `adapters/<name>.yaml`.

    Mandatory fields:
      schema_version: int — MUST equal CURRENT_SCHEMA_VERSION for v1
                            load. Future versions dispatch via loader.
      display_name: str — game name as it appears in brain prompts (via
                          {{ adapter.display_name }} template substitution)
      actions: dict[str, str] — logical action → scan-code key binding
                                e.g. {"forward": "W", "open_inventory": "E"}
      goal_grammars: dict[str, GoalGrammar] — named task templates
      world_facts: dict[str, str] — static per-game facts for brain
                                    context (e.g. {"axe_crafting":
                                    "planks + sticks in crafting_table"})

    Optional fields:
      inventory_ui: HUD geometry hints (dict, free-form for v1)
      perception: PerceptionConfig — tick rate + freshness budget
    """

    schema_version: int
    display_name: str
    actions: dict[str, str]
    goal_grammars: dict[str, GoalGrammar]
    world_facts: dict[str, str] = Field(default_factory=dict)
    inventory_ui: dict[str, Any] = Field(default_factory=dict)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Adapter schema_version {v} is not supported by this build "
                f"(expected {CURRENT_SCHEMA_VERSION}). See docs/adapter-schema.md "
                f"for migration."
            )
        return v

    @field_validator("actions")
    @classmethod
    def _non_empty_actions(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("actions must contain at least one binding")
        return v

    @field_validator("goal_grammars")
    @classmethod
    def _non_empty_goals(cls, v: dict[str, GoalGrammar]) -> dict[str, GoalGrammar]:
        if not v:
            raise ValueError("goal_grammars must contain at least one goal")
        return v


# Forward-refs for SuccessCheck self-reference
SuccessCheck.model_rebuild()
