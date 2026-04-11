"""SessionManager — single-session state + lifecycle tracking.

Phase C Step 1 iter-10 scope:

- Thread-safe state machine: idle → running → terminal(outcome)
- Generates session IDs (UUID4 slugified for filesystem safety)
- Owns the EventWriter instance (opened at start, closed on terminal)
- Tracks the current adapter and task description
- Emits session_start / session_aborted_* / session_complete envelopes
- Exposes `SessionInfo` snapshot for the /v1/state endpoint

Deliberately NOT in scope (deferred to later iters):

- Actual perception daemon thread — needs real WGC/DXGI bindings
- Brain wake dispatcher — needs real backend instances tied to the session
- Layer 2 trigger detector — depends on real perception stream
- Verify engine — depends on real tier-1 template assets
- Multi-session support — v1 is single-session per daemon instance

The separation is intentional: this module defines the state machine
and the state transitions fire the right events. When the real loops
land in later iters, they call `manager.transition_to_terminal(outcome)`
or `manager.is_running()` to coordinate without needing to know each
other's internals.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from gamemind.events.envelope import make_envelope
from gamemind.events.writer import EventWriter
from gamemind.session.outcomes import Outcome, is_terminal_outcome


SessionStatus = Literal["idle", "running", "terminal"]


@dataclass(frozen=True)
class SessionInfo:
    """Snapshot of the current session state. Returned by /v1/state."""

    session_id: str | None
    status: SessionStatus
    adapter_path: str | None
    task_description: str | None
    outcome: Outcome | None
    events_path: str | None
    started_at_monotonic_ns: int | None


@dataclass
class _SessionState:
    """Internal mutable state. Held under SessionManager._lock."""

    session_id: str | None = None
    status: SessionStatus = "idle"
    adapter_path: Path | None = None
    task_description: str | None = None
    outcome: str | None = None  # intentionally str, validated on set
    writer: EventWriter | None = None
    started_at_monotonic_ns: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionAlreadyRunningError(RuntimeError):
    """Raised when start() is called while another session is running.

    v1 is single-session per daemon. Multi-session support is a v2
    concern — see §OQ-6 in docs/final-design.md.
    """


class NoActiveSessionError(RuntimeError):
    """Raised when stop()/emit() is called with no session running."""


class SessionManager:
    """Thread-safe single-session manager.

    Usage from a FastAPI request handler:

        manager = app.state.session_manager
        info = manager.start(
            adapter_path=Path("adapters/minecraft.yaml"),
            task_description="chop 3 logs",
            runs_root=Path("runs"),
        )
        # ...
        manager.transition_to_terminal(outcome="success")

    The manager is thread-safe via a single `threading.Lock`. All
    mutations hold the lock for microseconds. No async — FastAPI
    dispatches to a threadpool for sync handlers, so this is safe
    under the default uvicorn event loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _SessionState()

    # ---------- public API ----------

    def snapshot(self) -> SessionInfo:
        """Return a snapshot of the current state. Safe to call any time."""
        with self._lock:
            s = self._state
            return SessionInfo(
                session_id=s.session_id,
                status=s.status,
                adapter_path=str(s.adapter_path) if s.adapter_path else None,
                task_description=s.task_description,
                outcome=s.outcome,  # type: ignore[arg-type]
                events_path=str(s.writer.events_path) if s.writer else None,
                started_at_monotonic_ns=s.started_at_monotonic_ns,
            )

    def is_running(self) -> bool:
        with self._lock:
            return self._state.status == "running"

    def start(
        self,
        *,
        adapter_path: Path,
        task_description: str,
        runs_root: Path,
    ) -> SessionInfo:
        """Start a new session. Opens an EventWriter and emits session_start.

        Raises:
            SessionAlreadyRunningError: if another session is already running.

        The caller is responsible for kicking off the real perception /
        action / brain loops after start() returns — this module only
        tracks state. Until the real loops land, start() + stop() is
        enough to exercise the full FastAPI surface and the event
        envelope schema.
        """
        import time  # noqa: PLC0415

        with self._lock:
            if self._state.status == "running":
                raise SessionAlreadyRunningError(
                    f"session {self._state.session_id} is already running; stop it first"
                )

            session_id = _new_session_id()
            session_dir = runs_root / session_id
            writer = EventWriter(session_dir)
            writer.start()

            self._state = _SessionState(
                session_id=session_id,
                status="running",
                adapter_path=adapter_path,
                task_description=task_description,
                writer=writer,
                started_at_monotonic_ns=time.monotonic_ns(),
            )

            # Emit session_start OUTSIDE the lock-critical section path
            # isn't necessary — envelope construction is O(us) and writer
            # enqueues non-blockingly. Keep it inside the lock so the
            # state transition and the event emission are atomic.
            envelope = make_envelope(
                session_id=session_id,
                producer="session",
                event_type="session_start",
                payload={
                    "adapter_path": str(adapter_path),
                    "task": task_description,
                },
            )
            writer.write(envelope)

            return SessionInfo(
                session_id=session_id,
                status="running",
                adapter_path=str(adapter_path),
                task_description=task_description,
                outcome=None,
                events_path=str(writer.events_path),
                started_at_monotonic_ns=self._state.started_at_monotonic_ns,
            )

    def transition_to_terminal(self, *, outcome: Outcome, **payload: Any) -> SessionInfo:
        """Terminate the current session with a named outcome.

        Emits the appropriate session_complete / session_aborted_*
        event, closes the EventWriter, and transitions state to
        terminal(outcome).

        Raises:
            NoActiveSessionError: if no session is running.
            ValueError: if `outcome` isn't a recognized Outcome literal.
        """
        if not is_terminal_outcome(outcome):
            raise ValueError(f"unknown outcome {outcome!r}; see gamemind.session.outcomes.Outcome")

        with self._lock:
            s = self._state
            if s.status != "running":
                raise NoActiveSessionError(f"cannot terminate: current status is {s.status!r}")
            assert s.writer is not None
            assert s.session_id is not None

            event_type = _event_type_for_outcome(outcome)
            envelope = make_envelope(
                session_id=s.session_id,
                producer="session",
                event_type=event_type,
                payload={"outcome": outcome, **payload},
            )
            s.writer.write(envelope)
            s.writer.close()

            snapshot = SessionInfo(
                session_id=s.session_id,
                status="terminal",
                adapter_path=str(s.adapter_path) if s.adapter_path else None,
                task_description=s.task_description,
                outcome=outcome,
                events_path=str(s.writer.events_path),
                started_at_monotonic_ns=s.started_at_monotonic_ns,
            )

            s.status = "terminal"
            s.outcome = outcome
            # Keep the other fields populated so /v1/state can show the
            # terminal state; caller can call reset() to go back to idle.

            return snapshot

    def reset(self) -> None:
        """Clear terminal state back to idle. Used after a terminated session
        has been acknowledged by the caller and we're ready for the next run."""
        with self._lock:
            if self._state.status == "running":
                raise SessionAlreadyRunningError(
                    "cannot reset while a session is running; terminate it first"
                )
            self._state = _SessionState()


# ---------- helpers ----------


def _new_session_id() -> str:
    """Generate a filesystem-safe session ID.

    UUID4 hex (32 chars, no hyphens) truncated to 12 chars for brevity.
    Collisions at 12 hex chars are still 1 in 2^48 — fine for single-
    user single-daemon v1.
    """
    return uuid.uuid4().hex[:12]


def _event_type_for_outcome(outcome: Outcome) -> str:
    """Map an Outcome to its events.jsonl event_type."""
    if outcome == "success":
        return "session_complete"
    if outcome == "runaway":
        return "session_aborted_runaway"
    if outcome == "perception_unavailable":
        return "session_aborted_perception_unavailable"
    if outcome == "brain_unavailable" or outcome == "brain_rate_limited":
        return "session_aborted_brain_unavailable"
    if outcome == "unhandled_exception":
        return "session_aborted_unhandled_exception"
    # All other outcomes (aborted, input_target_lost, capture_unavailable,
    # perception_disagreement_unresolvable, user_stopped) don't have a
    # dedicated event_type yet — they collapse to session_aborted_runaway
    # as a placeholder. Future iter expands the event_type enum.
    return "session_aborted_runaway"
