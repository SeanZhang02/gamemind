"""PyDirectInputBackend — real Layer 4 using pydirectinput-rgx scan codes.

Phase C Step 1 Batch A. Replaces the iter-1 stub (there wasn't one —
this module is new).

Implementation notes:

- pydirectinput-rgx (imported as `pydirectinput`) sends scan codes via
  `SendInput` with the `KEYEVENTF_SCANCODE` flag. That's the
  anti-cheat-safe path — virtual key codes are silently dropped by
  Minecraft and most anti-cheated games.
- pydirectinput does NOT support per-HWND targeting. SendInput always
  hits the current foreground window. The Protocol's `hwnd` parameter
  is used for focus verification (if the target isn't foreground, drop
  with `dropped_reason="focus_lost"`).
- Mouse clicks (`mouseleft` / `mouseright` / `mousemiddle`) are mapped
  to pydirectinput's click/mouseDown/mouseUp functions.
- `hold_ms > 0` on a `down=True` event is executed as
  `keyDown → sleep → keyUp` inside the backend. No matching release
  ScanCode needed.

Never raises from focus / send errors — returns InputResult with
`executed=False` and the appropriate drop reason.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from gamemind.input.backend import (
    InputBackend,
    InputResult,
    ScanCode,
    _hash_sequence,
)

_MOUSE_KEYS = {
    "mouseleft", "mouseright", "mousemiddle", "mouseleftdouble",
    "mouse_left", "mouse_right", "mouse_middle",
}


class PyDirectInputBackend:
    """Layer 4 input backend using `pydirectinput-rgx`."""

    def __init__(self) -> None:
        self._initialized = False
        self._init_error: str | None = None
        self._held_keys: set[str] = set()
        if sys.platform != "win32":
            self._init_error = "PyDirectInputBackend requires Windows"
            return
        try:
            import pydirectinput  # noqa: F401, PLC0415
        except ImportError as exc:
            self._init_error = f"pydirectinput not importable: {exc}"
            return
        self._initialized = True

    def send_scan_codes(
        self,
        hwnd: int,
        scan_code_sequence: list[ScanCode],
    ) -> InputResult:
        t0 = time.perf_counter()
        action_hash = _hash_sequence(scan_code_sequence)

        if not self._initialized:
            return InputResult(
                executed=False,
                dropped_reason="focus_lost",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={
                    "error": self._init_error or "not initialized",
                    "backend": "pydirectinput",
                },
            )

        # Focus check: pydirectinput hits whatever is foreground. If the
        # target HWND isn't foreground, drop with focus_lost.
        if hwnd > 0 and not _is_foreground(hwnd):
            return InputResult(
                executed=False,
                dropped_reason="focus_lost",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={
                    "backend": "pydirectinput",
                    "foreground_hwnd": _get_foreground_hwnd(),
                    "target_hwnd": hwnd,
                },
            )

        # Target-closed check: if the HWND itself is invalid, we still
        # can't send usefully (even though the foreground window is
        # something else, pydirectinput will blast keys into it).
        if hwnd > 0 and not _is_valid_hwnd(hwnd):
            return InputResult(
                executed=False,
                dropped_reason="target_closed",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={"backend": "pydirectinput", "hwnd": hwnd},
            )

        # Execute the sequence. pydirectinput's keyDown/keyUp/click
        # functions return True on success, False on failure. We treat
        # any False return as a drop.
        import pydirectinput  # noqa: PLC0415

        try:
            for event in scan_code_sequence:
                ok = _send_one(pydirectinput, event)
                if not ok:
                    return InputResult(
                        executed=False,
                        dropped_reason="rate_limit",  # pydirectinput rejected
                        action_hash=action_hash,
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        backend_meta={
                            "backend": "pydirectinput",
                            "failed_event": {
                                "key": event.key,
                                "down": event.down,
                                "hold_ms": event.hold_ms,
                            },
                        },
                    )
        except Exception as exc:  # noqa: BLE001
            return InputResult(
                executed=False,
                dropped_reason="rate_limit",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={
                    "backend": "pydirectinput",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

        return InputResult(
            executed=True,
            dropped_reason=None,
            action_hash=action_hash,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            backend_meta={"backend": "pydirectinput", "event_count": len(scan_code_sequence)},
        )

    def liveness(self) -> bool:
        return self._initialized

    def key_down(self, hwnd: int, key: str) -> None:
        """Press and hold a key. Key stays physically down until key_up() is called."""
        if key in self._held_keys:
            return
        import pydirectinput  # noqa: PLC0415

        key_lower = key.lower()
        if key_lower in _MOUSE_KEYS:
            button = {"mouse_left": "left", "mouse_right": "right", "mouse_middle": "middle"}.get(
                key_lower, "left"
            )
            pydirectinput.mouseDown(button=button)
        else:
            pydirectinput.keyDown(key)
        self._held_keys.add(key)

    def key_up(self, hwnd: int, key: str) -> None:
        """Release a previously held key."""
        import pydirectinput  # noqa: PLC0415

        key_lower = key.lower()
        if key_lower in _MOUSE_KEYS:
            button = {"mouse_left": "left", "mouse_right": "right", "mouse_middle": "middle"}.get(
                key_lower, "left"
            )
            pydirectinput.mouseUp(button=button)
        else:
            pydirectinput.keyUp(key)
        self._held_keys.discard(key)

    def release_all(self, hwnd: int) -> None:
        """Release all currently held keys. Called on shutdown/freeze for safety."""
        for key in list(self._held_keys):
            self.key_up(hwnd, key)

    def type_text(self, hwnd: int, text: str, *, interval_ms: float = 20.0) -> InputResult:
        """Type a plain string via pydirectinput.write.

        Convenience helper for `gamemind doctor --input` + for prompts
        that hand the daemon a literal string to type (chat commands,
        slash commands). NOT part of the Protocol — use send_scan_codes
        for anything the brain generates.
        """
        t0 = time.perf_counter()
        action_hash = _hash_sequence([ScanCode(key=f"type:{text}", down=True, hold_ms=0.0)])

        if not self._initialized:
            return InputResult(
                executed=False,
                dropped_reason="focus_lost",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={"error": self._init_error, "backend": "pydirectinput"},
            )

        if hwnd > 0 and not _is_foreground(hwnd):
            return InputResult(
                executed=False,
                dropped_reason="focus_lost",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={
                    "backend": "pydirectinput",
                    "foreground_hwnd": _get_foreground_hwnd(),
                    "target_hwnd": hwnd,
                },
            )

        import pydirectinput  # noqa: PLC0415

        try:
            pydirectinput.write(text, interval=interval_ms / 1000.0)
        except Exception as exc:  # noqa: BLE001
            return InputResult(
                executed=False,
                dropped_reason="rate_limit",
                action_hash=action_hash,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_meta={
                    "backend": "pydirectinput",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

        return InputResult(
            executed=True,
            dropped_reason=None,
            action_hash=action_hash,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            backend_meta={
                "backend": "pydirectinput",
                "text_length": len(text),
                "method": "write",
            },
        )


def _send_one(pydirectinput, event: ScanCode) -> bool:  # noqa: ANN001
    """Translate one ScanCode to a pydirectinput call."""
    key_lower = event.key.lower()

    if key_lower in _MOUSE_KEYS:
        button_map = {
            "mouseleft": "left",
            "mouseright": "right",
            "mousemiddle": "middle",
        }
        button = button_map.get(key_lower, "left")
        if event.down and event.hold_ms > 0:
            ok = pydirectinput.mouseDown(button=button)
            time.sleep(event.hold_ms / 1000.0)
            ok2 = pydirectinput.mouseUp(button=button)
            return bool(ok) and bool(ok2)
        if event.down:
            return bool(pydirectinput.mouseDown(button=button))
        return bool(pydirectinput.mouseUp(button=button))

    # Keyboard: pydirectinput.keyDown / keyUp take the lowercase key name
    if event.down and event.hold_ms > 0:
        ok = pydirectinput.keyDown(event.key)
        time.sleep(event.hold_ms / 1000.0)
        ok2 = pydirectinput.keyUp(event.key)
        return bool(ok) and bool(ok2)
    if event.down:
        return bool(pydirectinput.keyDown(event.key))
    return bool(pydirectinput.keyUp(event.key))


# ---------- Windows foreground / HWND validation helpers ----------


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
else:
    _user32 = None


def _get_foreground_hwnd() -> int:
    if _user32 is None:
        return 0
    return int(_user32.GetForegroundWindow())


def _is_foreground(hwnd: int) -> bool:
    return _get_foreground_hwnd() == hwnd


def _is_valid_hwnd(hwnd: int) -> bool:
    if _user32 is None:
        return False
    return bool(_user32.IsWindow(wintypes.HWND(hwnd)))


__all__ = ["InputBackend", "InputResult", "PyDirectInputBackend", "ScanCode"]
