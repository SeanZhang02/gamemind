"""Layer 4 — Action execution.

`InputBackend` Protocol + concrete `PyDirectInputBackend` wrapping
`pydirectinput-rgx`. Scan codes via SendInput, per the §OQ-3 anti-cheat
safe input stack (no Interception driver, no kernel hooks, no memory
reads — forward-compatible with Vanguard/EAC protected games).

Protocol signature frozen per Amendment A12 §OQ-3 addendum.
"""

from __future__ import annotations

from gamemind.input.backend import (
    InputBackend,
    InputResult,
    ScanCode,
    tap,
    press_and_release,
    hold,
)
from gamemind.input.pydirectinput_backend import PyDirectInputBackend

__all__ = [
    "InputBackend",
    "InputResult",
    "PyDirectInputBackend",
    "ScanCode",
    "hold",
    "press_and_release",
    "tap",
]
