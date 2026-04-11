"""EventWriter — thread-pool-backed JSONL append writer per Amendment A2.

Design:
  - Single background thread drains a queue and appends to disk
  - Callers (perception loop, brain loop, verify, action) enqueue
    envelopes non-blockingly via `write()`
  - Batched fsync: we write individual lines immediately but fsync
    only on session_complete / session_aborted_* to avoid per-tick
    fsync latency in the perception hot path
  - `scrub_secrets()` applied to payload before write
  - JSONL format: one JSON object per line, newline-terminated

Two output files per session:
  - `runs/<session>/events.jsonl` — every event
  - `runs/<session>/brain_calls.jsonl` — only `wake_*` + `brain_*`
    events (cheaper to scan for v2-T2 skill-compounding metric)

Concurrency: multi-producer, single-consumer. The consumer (writer
thread) is the only thing that touches the file descriptors. Producers
use `queue.Queue.put()` which is thread-safe.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

from gamemind.events.envelope import Envelope
from gamemind.events.scrub import scrub_secrets


_SESSION_TERMINAL_EVENTS = frozenset(
    {
        "session_complete",
        "session_aborted_runaway",
        "session_aborted_perception_unavailable",
        "session_aborted_brain_unavailable",
        "session_aborted_unhandled_exception",
    }
)

_BRAIN_CALL_PRODUCERS = frozenset({"brain"})
_BRAIN_CALL_EVENT_PREFIXES = ("wake_", "brain_")


def _is_brain_call(envelope: Envelope) -> bool:
    """True iff this envelope belongs in brain_calls.jsonl as well."""
    if envelope["producer"] not in _BRAIN_CALL_PRODUCERS:
        return False
    et = envelope["event_type"]
    return any(et.startswith(pfx) for pfx in _BRAIN_CALL_EVENT_PREFIXES)


class EventWriter:
    """Append-only JSONL writer for events.jsonl + brain_calls.jsonl.

    Usage:
        writer = EventWriter(session_dir=Path("runs/abc"))
        writer.start()
        writer.write(envelope)
        ...
        writer.write(terminal_envelope)  # e.g. session_complete
        writer.close()  # blocks until queue drains

    `close()` blocks until the background thread has drained all
    pending events and fsynced. After `close()`, subsequent `write()`
    calls silently drop (simpler than re-raising during shutdown).
    """

    _SENTINEL: Any = object()

    def __init__(self, session_dir: Path, *, queue_max: int = 4096) -> None:
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self.session_dir / "events.jsonl"
        self._brain_calls_path = self.session_dir / "brain_calls.jsonl"
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_max)
        self._thread: threading.Thread | None = None
        self._closed = False
        self._write_count = 0
        self._drop_count = 0

    def start(self) -> None:
        """Start the background writer thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._drain_loop,
            name=f"EventWriter-{self.session_dir.name}",
            daemon=True,
        )
        self._thread.start()

    def write(self, envelope: Envelope) -> bool:
        """Enqueue an envelope for writing. Non-blocking.

        Returns True iff the envelope was accepted. Returns False if
        the queue is full (backpressure — caller may want to log a
        drop metric) or the writer is closed.

        The envelope is scrubbed in the writer thread, not here, to
        keep the caller's hot path minimal.
        """
        if self._closed:
            return False
        try:
            self._queue.put_nowait(envelope)
            return True
        except queue.Full:
            self._drop_count += 1
            return False

    def close(self, *, timeout_s: float = 5.0) -> None:
        """Drain the queue, fsync, and stop the writer thread.

        Blocks up to `timeout_s` seconds waiting for the queue to
        drain. Subsequent calls are no-ops.
        """
        if self._closed:
            return
        self._closed = True
        # Poison pill tells the drain loop to exit after processing
        # everything currently in the queue.
        self._queue.put(self._SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)

    @property
    def write_count(self) -> int:
        return self._write_count

    @property
    def drop_count(self) -> int:
        return self._drop_count

    @property
    def events_path(self) -> Path:
        return self._events_path

    @property
    def brain_calls_path(self) -> Path:
        return self._brain_calls_path

    def _drain_loop(self) -> None:
        """Background thread body. Drains the queue until sentinel."""
        # Open both files in append binary mode. We write UTF-8-encoded
        # JSON ourselves so we control newline handling (no CRLF on
        # Windows even though that's the default for text mode).
        with (
            self._events_path.open("ab") as events_f,
            self._brain_calls_path.open("ab") as brain_f,
        ):
            while True:
                item = self._queue.get()
                if item is self._SENTINEL:
                    events_f.flush()
                    brain_f.flush()
                    try:
                        import os  # noqa: PLC0415

                        os.fsync(events_f.fileno())
                        os.fsync(brain_f.fileno())
                    except OSError:
                        pass
                    return
                self._write_one(item, events_f, brain_f)

    def _write_one(
        self,
        envelope: Envelope,
        events_f: Any,
        brain_f: Any,
    ) -> None:
        """Serialize one envelope and append it to the appropriate files."""
        scrubbed_payload = scrub_secrets(envelope["payload"])
        out: dict[str, Any] = dict(envelope)
        out["payload"] = scrubbed_payload
        line = (json.dumps(out, ensure_ascii=False) + "\n").encode("utf-8")
        events_f.write(line)
        if _is_brain_call(envelope):
            brain_f.write(line)
        self._write_count += 1
        # Flush on session terminal events so a crashed daemon doesn't
        # lose the "how did the session end" signal.
        if envelope["event_type"] in _SESSION_TERMINAL_EVENTS:
            events_f.flush()
            brain_f.flush()
            try:
                import os  # noqa: PLC0415

                os.fsync(events_f.fileno())
                os.fsync(brain_f.fileno())
            except OSError:
                pass

    def __enter__(self) -> EventWriter:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
