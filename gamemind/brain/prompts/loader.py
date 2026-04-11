"""Template loader using string.Template for $-style placeholders.

Deliberately uses Python's built-in `string.Template` (zero new deps)
rather than Jinja2. Templates use `$name` and `${name}` placeholders;
`safe_substitute()` leaves unknown placeholders intact rather than
raising, which keeps templates forward-compatible when the assembler
gains new context keys.

Upgrade path to Jinja2 (loops, conditionals, filters): swap this loader
for a Jinja2 environment. All `.prompt` files stay the same since `$name`
isn't valid Jinja syntax and won't accidentally trigger rendering.

Amendment A3 Design Rule 4: this loader does NOT auto-wrap adapter text
in observation tags. The ASSEMBLER layer (prompt_assembler.py) is
responsible for applying observation tags to untrusted content before
passing it as template kwargs.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

TEMPLATE_DIR = Path(__file__).parent / "templates"

# The 5 Layer 3 wake triggers per §1.4. One template per trigger.
TEMPLATE_NAMES: tuple[str, ...] = (
    "plan_decomposition",  # W1: task start
    "replan_from_stuck",  # W2: stuck detector fired
    "abort_evaluation",  # W3: abort condition or stalled success
    "disagreement_arbiter",  # W4: vision critic escalation
    "task_completion_verification",  # W5: success check final verify
)


def list_templates() -> list[str]:
    """Return the list of available template names (without .prompt extension).

    Only reads files that match the known TEMPLATE_NAMES set — extras
    in the directory are ignored so the contract is stable.
    """
    available: list[str] = []
    for name in TEMPLATE_NAMES:
        if (TEMPLATE_DIR / f"{name}.prompt").exists():
            available.append(name)
    return available


def render_template(name: str, **context: object) -> str:
    """Render the named template with $-substituted context.

    Unknown placeholders are left intact (safe_substitute). This is
    intentional: it lets callers omit fields they don't have without
    the renderer exploding, and the uninterpolated `$foo` shows up
    clearly in the output for debugging.

    Raises FileNotFoundError if the template doesn't exist — hard error
    so typos fail fast rather than silently rendering an empty string.
    """
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown template {name!r}; expected one of {TEMPLATE_NAMES}")
    path = TEMPLATE_DIR / f"{name}.prompt"
    text = path.read_text(encoding="utf-8")
    return Template(text).safe_substitute(**context)
