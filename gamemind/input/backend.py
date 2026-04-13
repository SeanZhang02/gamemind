"""InputBackend Protocol + ScanCode / InputResult dataclasses.

Amendment A12 §OQ-3 addendum freezes the shape. Implementations:

- `gamemind.input.pydirectinput_backend.PyDirectInputBackend` — v1
  default, uses `pydirectinput-rgx` for SendInput scan codes.
- (Future) a D3 fallback backend if pydirectinput-rgx regresses.

Scan codes are the anti-cheat-safe path. Virtual key codes (VK_*) are
silently dropped by many games (Minecraft included per §OQ-3), which
is why we never use them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, Protocol

DropReason = Literal["focus_lost", "target_closed", "rate_limit"]


@dataclass(frozen=True)
class ScanCode:
    """One scan-code event for `InputBackend.send_scan_codes`.

    Fields:
      key: pydirectinput-compatible key name ("w", "space", "leftshift",
           "f3", "mouseleft", etc.) — case-insensitive, validated at
           send time by pydirectinput itself.
      down: True = press, False = release. For a simple "tap W" you
            emit two ScanCodes: `{down=True, hold_ms=0}` then
            `{down=False, hold_ms=0}`.
      hold_ms: if >0 on a `down=True` event, backend sleeps this many
               ms BEFORE emitting the event. This lets callers sequence
               a "press W, hold 800ms, release W" as three events:
               `[down W hold 0, up W hold 800, ...]` — wait no, that's
               wrong. Let me use the simpler convention:
               - A single `down=True, hold_ms=N` event means:
                 key-down, sleep N ms, key-up.
               - The caller does NOT need to emit a matching up event
                 for hold_ms > 0 events.
               - For fine-grained control (hold W indefinitely across
                 multiple ticks), use `down=True, hold_ms=0` + later
                 `down=False, hold_ms=0`.
    """

    key: str
    down: bool
    hold_ms: float = 0.0


@dataclass
class InputResult:
    """Result of an `InputBackend.send_scan_codes` call.

    Fields:
      executed: True iff the full sequence completed without drop.
      dropped_reason: if executed=False, names the drop category. Used
                      by Layer 2 action repetition guard (A13) + event
                      writer (A2 `action_dropped_*` event types).
      action_hash: stable SHA256-trunc8 hash over the scan code sequence.
                   Used by A13 guard to detect repeated identical
                   actions within a 10-second window.
      latency_ms: wall clock ms from call start to return.
    """

    executed: bool
    dropped_reason: DropReason | None
    action_hash: str
    latency_ms: float
    backend_meta: dict = field(default_factory=dict)


def _hash_sequence(sequence: list[ScanCode]) -> str:
    """Stable hash over a scan code sequence for A13 action repetition guard."""
    payload = "|".join(f"{c.key}:{int(c.down)}:{c.hold_ms:.1f}" for c in sequence)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class InputBackend(Protocol):
    """Send scan-code sequences to a target HWND.

    Implementations MUST:
      - Never raise from focus / target-closed / rate-limit errors.
        Return InputResult with executed=False and the appropriate
        dropped_reason instead, so the Layer 2 action repetition guard
        and the events.jsonl writer can route recovery.
      - Compute `action_hash` via `_hash_sequence()` over the input
        regardless of success, so A13 sees dropped actions too.

    Implementations MAY:
      - Use pydirectinput-rgx (default)
      - Use a custom SendInput call if pydirectinput regresses
      - Enforce a rate limit (action_hash repetition) internally
        before forwarding to pydirectinput

    The `hwnd` parameter exists to let backends verify focus before
    sending. pydirectinput itself doesn't support per-HWND targeting
    (all SendInput calls hit the current foreground window), so the
    PyDirectInputBackend uses `hwnd` for focus verification only.
    """

    def send_scan_codes(
        self,
        hwnd: int,
        scan_code_sequence: list[ScanCode],
    ) -> InputResult: ...

    def key_down(self, hwnd: int, key: str) -> None:
        """Press and hold a key. Key stays physically down until key_up() is called."""
        ...

    def key_up(self, hwnd: int, key: str) -> None:
        """Release a previously held key."""
        ...

    def release_all(self, hwnd: int) -> None:
        """Release all currently held keys. Called on shutdown/freeze for safety."""
        ...

    def liveness(self) -> bool: ...


# ---------- convenience helpers for building sequences ----------


def tap(key: str) -> list[ScanCode]:
    """Instant press+release, no hold."""
    return [
        ScanCode(key=key, down=True, hold_ms=0.0),
        ScanCode(key=key, down=False, hold_ms=0.0),
    ]


def press_and_release(key: str, hold_ms: float) -> list[ScanCode]:
    """Press, hold for `hold_ms` milliseconds, release.

    Canonical form as a single ScanCode with hold_ms set.
    """
    return [ScanCode(key=key, down=True, hold_ms=hold_ms)]


def hold(key: str) -> list[ScanCode]:
    """Press key without releasing. Caller must emit a matching release."""
    return [ScanCode(key=key, down=True, hold_ms=0.0)]


def release(key: str) -> list[ScanCode]:
    """Release a previously held key."""
    return [ScanCode(key=key, down=False, hold_ms=0.0)]


__all__ = [
    "DropReason",
    "InputBackend",
    "InputResult",
    "ScanCode",
    "_hash_sequence",
    "hold",
    "press_and_release",
    "release",
    "tap",
]
