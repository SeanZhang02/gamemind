"""Session management — tracks a single active agent session.

Per Phase C Step 1 iter-10, this module owns:

- Session lifecycle state (idle → running → complete/aborted)
- EventWriter instance (one per session, opened at start, closed on
  terminal event per Amendment A2)
- Adapter reference (loaded once per session)
- Session-scoped event stream via `emit()` helper

Real perception / action / brain loops are NOT in this module yet —
those land in a subsequent iter when the Layer 0/1/4 bindings are real
enough to drive end-to-end. This is deliberately a THIN session shell
so the daemon's HTTP endpoints have a coherent state machine to talk
to from day 1.
"""

from __future__ import annotations

from gamemind.session.manager import SessionInfo, SessionManager
from gamemind.session.outcomes import Outcome, is_terminal_outcome

__all__ = [
    "Outcome",
    "SessionInfo",
    "SessionManager",
    "is_terminal_outcome",
]
