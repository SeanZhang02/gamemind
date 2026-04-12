"""Live integration test for PyDirectInputBackend.

Targets a tkinter Entry widget in a subprocess (NOT Notepad — Windows 11
Notepad routes through the Text Services Framework and Chinese Pinyin
IME will intercept letter keys before they reach the edit control, even
when they're sent as scan codes via SendInput).

tkinter's Entry widget is a classic Win32 EDIT control and receives raw
scan codes directly.

Flow:
    1. Spawn tkinter child showing an Entry widget with a unique title
    2. Child polls Entry content to a temp file every 100ms
    3. Parent waits for window + forces it to foreground
    4. Parent: PyDirectInputBackend.type_text("hello gamemind 123")
    5. Parent: Ctrl+A (selects all in Entry — no-op for verification,
       but exercises the send_scan_codes path)
    6. Parent: sleep, read temp file, assert Entry text matches
    7. Kill child process
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from gamemind.input.backend import ScanCode  # noqa: E402
from gamemind.input.pydirectinput_backend import PyDirectInputBackend  # noqa: E402

TEST_TEXT = "hello gamemind 123"
SURROGATE_TITLE = f"gamemind-input-surrogate-{os.getpid()}"
OUTPUT_FILE = Path(
    f"C:/Users/33735/AppData/Local/Temp/gamemind-input-output-{os.getpid()}.txt"
)


CHILD_SCRIPT = f"""
import ctypes
import sys
import tkinter as tk
from pathlib import Path

# CRITICAL: disable IME for this entire process BEFORE Tk starts. On
# Chinese Windows, Microsoft Pinyin IME attaches to every input
# context by default and intercepts Latin letters + space as
# composition input. ImmDisableIME(-1) = disable IME for all threads
# in the current process.
try:
    imm32 = ctypes.WinDLL("imm32")
    imm32.ImmDisableIME.argtypes = [ctypes.c_ulong]
    imm32.ImmDisableIME.restype = ctypes.c_int
    imm32.ImmDisableIME(ctypes.c_ulong(0xFFFFFFFF))  # -1 as DWORD
except Exception:
    pass

TITLE = "{SURROGATE_TITLE}"
OUTPUT = Path(r"{OUTPUT_FILE!s}")

root = tk.Tk()
root.title(TITLE)
root.geometry("600x200+300+300")
root.configure(bg="#f0f0f0")

label = tk.Label(
    root,
    text="gamemind input test target",
    font=("Segoe UI", 14),
    bg="#f0f0f0",
)
label.pack(pady=10)

entry = tk.Entry(root, font=("Consolas", 20), width=40)
entry.pack(pady=20, padx=20)
entry.focus_set()

def poll():
    try:
        OUTPUT.write_text(entry.get(), encoding="utf-8")
    except Exception:
        pass
    root.after(100, poll)

def exit_after():
    root.quit()

root.after(50, poll)
root.after(6000, exit_after)

# Aggressive foregrounding on startup so focus lands on us
root.attributes("-topmost", True)
root.lift()
root.update_idletasks()
root.deiconify()
root.focus_force()
entry.focus_set()
# Drop topmost after a bit so we don't block the parent from its own windows
root.after(300, lambda: root.attributes("-topmost", False))

root.mainloop()
sys.exit(0)
"""


def _log(msg: str) -> None:
    print(f"[test_input_live] {msg}", flush=True)


def _fail(msg: str) -> None:
    _log(f"FAIL: {msg}")
    raise SystemExit(1)


def _set_foreground(hwnd: int) -> bool:
    """Force a window to the foreground, bypassing Windows focus restrictions."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.keybd_event.argtypes = [
        ctypes.c_ubyte,
        ctypes.c_ubyte,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    user32.keybd_event.restype = None

    user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    # Dummy Alt press+release grants foreground privilege
    user32.keybd_event(0x12, 0, 0, None)
    user32.keybd_event(0x12, 0, 0x0002, None)
    user32.BringWindowToTop(wintypes.HWND(hwnd))
    return bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))


def _find_window(title: str, timeout_s: float = 3.0) -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = user32.FindWindowW(None, title)
        if raw:
            return int(raw)
        time.sleep(0.1)
    return 0


def main() -> int:
    _log(f"TEST_TEXT = {TEST_TEXT!r}")
    _log(f"output file: {OUTPUT_FILE}")

    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()

    _log("spawning tkinter surrogate (Win32 EDIT, bypasses IME)...")
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _log("waiting for surrogate window...")
        hwnd = _find_window(SURROGATE_TITLE, timeout_s=4.0)
        if hwnd == 0:
            stderr = proc.stderr.read(2000).decode("utf-8", errors="replace")
            _fail(f"surrogate HWND not found. Child stderr:\n{stderr}")
        _log(f"HWND: {hwnd} (0x{hwnd:08x})")

        time.sleep(0.3)
        _log("forcing foreground...")
        _set_foreground(hwnd)
        time.sleep(0.3)

        # Verify foreground
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        fg_raw = user32.GetForegroundWindow()
        fg = int(fg_raw) if fg_raw else 0
        _log(f"foreground HWND: {fg} (want {hwnd})")
        if fg != hwnd:
            _log(f"WARNING: foreground is {fg}, not {hwnd} — input may go elsewhere")

        backend = PyDirectInputBackend()
        if not backend.liveness():
            _fail(f"PyDirectInputBackend unhealthy: {backend._init_error}")
        _log("PyDirectInputBackend OK")

        # ---------- type_text path ----------
        _log(f"type_text({TEST_TEXT!r})...")
        type_result = backend.type_text(hwnd, TEST_TEXT, interval_ms=20.0)
        _log(
            f"  executed={type_result.executed} "
            f"dropped_reason={type_result.dropped_reason} "
            f"latency={type_result.latency_ms:.1f}ms"
        )
        if not type_result.executed:
            _fail(f"type_text dropped: {type_result.dropped_reason} meta={type_result.backend_meta}")

        # Let the surrogate poll the entry at least once more after our writes
        time.sleep(0.3)
        typed_result = OUTPUT_FILE.read_text(encoding="utf-8") if OUTPUT_FILE.exists() else ""
        _log(f"  entry after type_text: {typed_result!r}")

        if typed_result != TEST_TEXT:
            _fail(
                f"type_text produced wrong entry content.\n"
                f"  expected: {TEST_TEXT!r}\n"
                f"  got:      {typed_result!r}\n"
                f"  (foreground was {fg}, target was {hwnd})"
            )
        _log("  type_text MATCH")

        # ---------- send_scan_codes path: tap space bar a few times ----------
        # Use a simple scan_code sequence that we can verify unambiguously.
        # tap space 3 times → entry should have 3 extra spaces appended.
        _log("sending 3 space-bar taps via send_scan_codes...")
        space_taps = []
        for _ in range(3):
            space_taps.append(ScanCode(key="space", down=True, hold_ms=0.0))
            space_taps.append(ScanCode(key="space", down=False, hold_ms=0.0))
        hotkey_result = backend.send_scan_codes(hwnd, space_taps)
        _log(
            f"  executed={hotkey_result.executed} "
            f"dropped_reason={hotkey_result.dropped_reason} "
            f"latency={hotkey_result.latency_ms:.1f}ms "
            f"action_hash={hotkey_result.action_hash}"
        )
        if not hotkey_result.executed:
            _fail(f"space taps dropped: {hotkey_result.dropped_reason}")

        time.sleep(0.3)
        final_result = OUTPUT_FILE.read_text(encoding="utf-8") if OUTPUT_FILE.exists() else ""
        _log(f"  entry after 3 space taps: {final_result!r}")

        expected_final = TEST_TEXT + "   "
        if final_result != expected_final:
            _fail(
                f"send_scan_codes produced wrong entry content.\n"
                f"  expected: {expected_final!r}\n"
                f"  got:      {final_result!r}"
            )
        _log("  send_scan_codes MATCH")

        _log("")
        _log("=" * 60)
        _log("BATCH A INPUT VERIFICATION: PASS")
        _log("=" * 60)
        _log(f"  type_text:        {type_result.latency_ms:.0f}ms for {len(TEST_TEXT)} chars")
        _log(f"  send_scan_codes:  {hotkey_result.latency_ms:.0f}ms for 6 events (3 space taps)")
        _log(f"  final entry:      {final_result!r}")
        return 0

    finally:
        _log("killing surrogate...")
        try:
            proc.terminate()
            proc.wait(timeout=3.0)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001, S110
                pass
        if OUTPUT_FILE.exists():
            try:
                OUTPUT_FILE.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
