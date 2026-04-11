"""Prompt templates for Layer 3 brain wake triggers.

Per §1.4 of docs/final-design.md, the brain is woken on 5 semantic
triggers (W1-W5). Each trigger has a dedicated prompt template under
`templates/`. The assembler renders templates with per-adapter +
per-wake context.

Rule 3 (docs/final-design.md §3): every template is game-agnostic. No
game names, no game-specific mechanics terms — adapter data is injected
at render time. CI `scripts/lint_observation_tags.py` enforces this.
"""

from __future__ import annotations

from gamemind.brain.prompts.loader import (
    TEMPLATE_DIR,
    TEMPLATE_NAMES,
    list_templates,
    render_template,
)

__all__ = [
    "TEMPLATE_DIR",
    "TEMPLATE_NAMES",
    "list_templates",
    "render_template",
]
