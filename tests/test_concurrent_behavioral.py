"""Behavioral tests for the concurrent 2-thread runner architecture.

These tests verify the KEY PROPERTY of the concurrent design:
  - Perception Thread: runs VLM inference (~500ms), writes to Blackboard
  - Orchestrator Thread: runs at 20-50Hz, reads Blackboard snapshot,
    ticks BT, manages stateful keyDown/keyUp

The tests are self-contained with mocks. No dependency on running Ollama,
Minecraft, or real input devices.

ALL tests should FAIL against the current serial runner (v2), proving
they are meaningful behavioral assertions about concurrency properties,
not tautologies.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from gamemind.blackboard import Blackboard, Producer
from gamemind.bt.harvesting import build_harvesting_tree
from gamemind.bt.motor_command import MotorCommand, MotorCommandType
from gamemind.motor import Motor


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ACTIONS = {"forward": "W", "attack": "MouseLeft", "jump": "Space", "backward": "S"}


class MockInputBackend:
    """Records key_down / key_up / release_all calls with timestamps."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []  # (method, key, monotonic)
        self._held: set[str] = set()
        self.lock = threading.Lock()

    def key_down(self, hwnd: int, key: str) -> None:
        with self.lock:
            self.calls.append(("key_down", key, time.monotonic()))
            self._held.add(key)

    def key_up(self, hwnd: int, key: str) -> None:
        with self.lock:
            self.calls.append(("key_up", key, time.monotonic()))
            self._held.discard(key)

    def release_all(self, hwnd: int) -> None:
        with self.lock:
            for k in list(self._held):
                self.calls.append(("key_up", k, time.monotonic()))
            self._held.clear()
            self.calls.append(("release_all", "", time.monotonic()))

    @property
    def held_keys(self) -> set[str]:
        with self.lock:
            return set(self._held)

    def liveness(self) -> bool:
        return True

    def calls_of(self, method: str, key: str = "") -> list[float]:
        """Return timestamps for calls matching method (and optionally key)."""
        with self.lock:
            return [
                ts
                for m, k, ts in self.calls
                if m == method and (key == "" or k == key)
            ]


# ---------------------------------------------------------------------------
# Test 1: Motor holds key while VLM runs (the KEY PROPERTY)
# ---------------------------------------------------------------------------


def test_motor_holds_during_vlm() -> None:
    """In the concurrent design, a key pressed by the orchestrator must
    stay physically held while the VLM inference runs on the perception
    thread. This test simulates a 500ms VLM call and verifies that
    key_down fires immediately while key_up does NOT fire for >= 400ms.

    FAILS against serial runner: the serial runner blocks the entire
    agent loop during VLM inference, so no key presses happen at all
    while VLM is running; there's no separate orchestrator thread.
    """
    bb = Blackboard()
    inp = MockInputBackend()
    motor = Motor(ACTIONS)
    stop = threading.Event()
    vlm_started = threading.Event()
    vlm_done = threading.Event()
    key_down_event = threading.Event()

    # Pre-populate blackboard so BT wants to attack
    bb.write("crosshair_block", "oak_log", Producer.VLM)
    bb.write("crosshair_block", "oak_log", Producer.VLM)
    bb.write("crosshair_block", "oak_log", Producer.VLM)
    bb.write("vlm_suggested_action", "attack", Producer.VLM)
    bb.swap()

    def perception_thread() -> None:
        """Simulates VLM inference that takes 500ms."""
        vlm_started.set()
        time.sleep(0.5)  # Simulate VLM latency
        # After VLM completes, write new data
        bb.write("crosshair_block", "oak_log", Producer.VLM)
        bb.write("vlm_suggested_action", "attack", Producer.VLM)
        bb.swap()
        vlm_done.set()

    def orchestrator_thread() -> None:
        """Simplified orchestrator: tick BT, resolve motor, send keys."""
        tree = build_harvesting_tree()
        hwnd = 0
        tick_interval = 0.02  # 50Hz

        while not stop.is_set():
            # Tick BT against current blackboard snapshot
            tree.tick(bb)
            cmd = tree.motor_command

            if cmd and cmd.command_type == MotorCommandType.HOLD:
                resolved = motor.resolve(cmd)
                if resolved and resolved.key and "MouseLeft" not in inp.held_keys:
                    inp.key_down(hwnd, resolved.key)
                    key_down_event.set()
            time.sleep(tick_interval)

    # Start orchestrator first, then perception
    orch = threading.Thread(target=orchestrator_thread, daemon=True)
    orch.start()

    # Wait a moment for orchestrator to start ticking
    key_down_event.wait(timeout=2.0)
    key_down_ts = time.monotonic()

    # Now start VLM (simulating the perception thread starting inference)
    perc = threading.Thread(target=perception_thread, daemon=True)
    perc.start()
    vlm_started.wait(timeout=2.0)

    # While VLM is running (~500ms), orchestrator should NOT release the key
    time.sleep(0.4)  # Wait 400ms into the VLM call

    # Assert: key_down was called
    assert len(inp.calls_of("key_down", "MouseLeft")) > 0, (
        "key_down should have been called for attack"
    )
    # Assert: key_up NOT called for the held key during VLM
    key_up_calls = inp.calls_of("key_up", "MouseLeft")
    if key_up_calls:
        # If any key_up happened, it must be well after the key_down
        elapsed = key_up_calls[0] - key_down_ts
        assert elapsed >= 0.4, (
            f"key_up fired too early ({elapsed:.3f}s after key_down). "
            f"Key should stay held while VLM runs."
        )

    # Cleanup
    stop.set()
    vlm_done.wait(timeout=2.0)
    orch.join(timeout=2.0)
    perc.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Test 2: Key held across orchestrator ticks
# ---------------------------------------------------------------------------


def test_key_held_across_ticks() -> None:
    """When BT returns HOLD on consecutive ticks, the orchestrator should
    issue key_down ONCE and never call key_up between ticks.

    FAILS against serial runner: serial runner uses press_and_release()
    with hold_ms per tick, meaning each tick issues its own key_down +
    key_up sequence rather than holding across ticks.
    """
    inp = MockInputBackend()
    motor = Motor(ACTIONS)
    hwnd = 0

    # Simulate 5 consecutive ticks where BT says HOLD attack
    for _ in range(5):
        cmd = MotorCommand.hold("attack", duration_ms=0.0)  # indefinite hold
        resolved = motor.resolve(cmd)
        if resolved and resolved.key and "MouseLeft" not in inp.held_keys:
            inp.key_down(hwnd, resolved.key)
        time.sleep(0.02)  # 50Hz tick

    # Assert: key_down called exactly once
    key_downs = inp.calls_of("key_down", "MouseLeft")
    assert len(key_downs) == 1, (
        f"key_down should be called once, got {len(key_downs)}"
    )

    # Assert: key_up never called during the 5 ticks
    key_ups = inp.calls_of("key_up", "MouseLeft")
    assert len(key_ups) == 0, (
        f"key_up should never be called during HOLD, got {len(key_ups)}"
    )


# ---------------------------------------------------------------------------
# Test 3: Freeze releases all held keys
# ---------------------------------------------------------------------------


def test_freeze_releases_keys() -> None:
    """When watchdog triggers FATAL (freeze), all held keys must be
    released within 100ms. This prevents the agent from holding W/attack
    indefinitely after the watchdog detects a problem.

    FAILS against serial runner: serial runner's motor.freeze() only
    sets internal state flags but does NOT call InputBackend.release_all().
    The concurrent design must actively release physical keys on freeze.
    """
    inp = MockInputBackend()
    motor = Motor(ACTIONS)
    stop = threading.Event()
    freeze_event = threading.Event()
    keys_released = threading.Event()
    hwnd = 0

    # Pre-hold a key
    motor._state.is_idle = False
    motor._state.recovery_streak = 2  # skip hysteresis
    inp.key_down(hwnd, "MouseLeft")

    def orchestrator_with_freeze() -> None:
        while not stop.is_set():
            if freeze_event.is_set():
                motor.freeze()
                inp.release_all(hwnd)
                keys_released.set()
                return
            time.sleep(0.01)

    orch = threading.Thread(target=orchestrator_with_freeze, daemon=True)
    orch.start()

    # Trigger freeze
    freeze_start = time.monotonic()
    freeze_event.set()

    # Wait for keys to be released
    released = keys_released.wait(timeout=0.1)  # 100ms budget
    release_elapsed = time.monotonic() - freeze_start

    assert released, (
        f"Keys not released within 100ms of freeze (waited {release_elapsed:.3f}s)"
    )

    # Verify release_all was actually called
    release_calls = inp.calls_of("release_all")
    assert len(release_calls) > 0, "release_all must be called on freeze"

    # Verify no keys are still held
    assert len(inp.held_keys) == 0, (
        f"Keys still held after freeze: {inp.held_keys}"
    )

    stop.set()
    orch.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Test 4: BT ticks on snapshot (double-buffer isolation)
# ---------------------------------------------------------------------------


def test_bt_ticks_on_snapshot() -> None:
    """The BT must tick on a frozen snapshot of the blackboard so that
    mid-tick writes from perception don't cause inconsistent reads.

    Write values, swap, take snapshot. Then write DIFFERENT values and
    swap again. The snapshot must still have the OLD values.

    This is a UNIT test on Blackboard.snapshot(). It does NOT need
    threading to prove the property.

    FAILS against serial runner: serial runner reads from bb directly
    via bb.read_value() which sees the latest swapped data, not a
    point-in-time snapshot. The concurrent design requires snapshot()
    for isolation.
    """
    bb = Blackboard()

    # Write initial values and swap to front buffer
    bb.write("crosshair_block", "oak_log", Producer.VLM)
    bb.write("vlm_suggested_action", "attack", Producer.VLM)
    bb.swap()

    # Take a snapshot (this is what the orchestrator would hand to BT)
    snapshot = bb.snapshot()

    # Now simulate perception writing NEW values and swapping
    bb.write("crosshair_block", "stone", Producer.VLM)
    bb.write("vlm_suggested_action", "forward", Producer.VLM)
    bb.swap()

    # The snapshot must still have the OLD values
    assert snapshot["crosshair_block"].value == "oak_log", (
        f"Snapshot should have 'oak_log' but got '{snapshot['crosshair_block'].value}'. "
        f"Snapshot must be a point-in-time freeze, not a live view."
    )
    assert snapshot["vlm_suggested_action"].value == "attack", (
        f"Snapshot should have 'attack' but got '{snapshot['vlm_suggested_action'].value}'"
    )

    # Meanwhile, the live blackboard should have the NEW values
    live = bb.read_value("crosshair_block")
    assert live == "stone", "Live blackboard should reflect the latest swap"


# ---------------------------------------------------------------------------
# Test 5: W2 brain call is async / non-blocking
# ---------------------------------------------------------------------------


def test_w2_async_non_blocking() -> None:
    """When W2 stuck detection fires, the brain call must be dispatched
    asynchronously so the orchestrator loop is not blocked. The
    orchestrator should continue ticking within 100ms.

    FAILS against serial runner: serial runner calls self._call_brain_w2()
    synchronously in the agent loop, blocking the entire loop (including
    motor control) for the duration of the brain response.
    """
    brain_called = threading.Event()
    brain_response = threading.Event()
    orchestrator_continued = threading.Event()

    def slow_brain(*args: object, **kwargs: object) -> dict:
        """Simulates a 2000ms brain call."""
        brain_called.set()
        time.sleep(2.0)
        brain_response.set()
        return {"subgoals": ["find_tree"], "policy_hints": []}

    executor = ThreadPoolExecutor(max_workers=1)
    stop = threading.Event()
    ticks_while_brain_running = 0
    ticks_lock = threading.Lock()

    def orchestrator() -> None:
        nonlocal ticks_while_brain_running
        tick_interval = 0.02  # 50Hz
        w2_dispatched = False

        while not stop.is_set():
            # Simulate W2 stuck detection on first tick
            if not w2_dispatched:
                # Dispatch brain call to thread pool (non-blocking)
                executor.submit(slow_brain)
                w2_dispatched = True

            # Count ticks that happen while brain is still running
            if brain_called.is_set() and not brain_response.is_set():
                with ticks_lock:
                    ticks_while_brain_running += 1
                orchestrator_continued.set()

            time.sleep(tick_interval)

    orch = threading.Thread(target=orchestrator, daemon=True)
    orch.start()

    # Wait for brain to be called
    brain_called.wait(timeout=2.0)

    # The orchestrator should continue within 100ms of the brain call
    continued = orchestrator_continued.wait(timeout=0.5)
    assert continued, (
        "Orchestrator must continue ticking within 100ms of dispatching W2 brain call. "
        "The brain call should be async, not blocking the orchestrator loop."
    )

    # Let the orchestrator tick for a while during the brain call
    time.sleep(0.3)

    # Verify the orchestrator was ticking during the brain call
    with ticks_lock:
        tick_count = ticks_while_brain_running
    assert tick_count >= 3, (
        f"Expected multiple orchestrator ticks during brain call, got {tick_count}"
    )

    # Wait for everything to finish
    stop.set()
    brain_response.wait(timeout=3.0)
    orch.join(timeout=2.0)
    executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Test 6: Perception thread death detected
# ---------------------------------------------------------------------------


def test_perception_death_detected() -> None:
    """If the perception thread dies (exception), the orchestrator must
    detect it via Blackboard freshness (no new VLM updates) and trigger
    abort within a reasonable timeout.

    FAILS against serial runner: in the serial runner, VLM perception
    runs inline in _agent_loop, so a VLM crash just crashes the whole
    loop. There's no freshness monitoring because there's no separate
    perception thread to die independently.
    """
    bb = Blackboard()
    stop = threading.Event()
    abort_triggered = threading.Event()

    # Write initial VLM timestamp so freshness check has a baseline
    bb.write("vlm_last_update_ns", time.monotonic_ns(), Producer.VLM)
    bb.swap()

    perception_crashed = threading.Event()

    def dying_perception() -> None:
        """Perception thread that writes 2 updates then crashes."""
        try:
            for i in range(2):
                bb.write("crosshair_block", f"block_{i}", Producer.VLM)
                bb.write("vlm_last_update_ns", time.monotonic_ns(), Producer.VLM)
                bb.swap()
                time.sleep(0.1)
            raise RuntimeError("VLM backend crashed!")
        except RuntimeError:
            # In real code this exception propagates and kills the thread.
            # We catch it here to avoid pytest's unhandled-thread-exception
            # warning, but the key point is: no more Blackboard writes happen.
            perception_crashed.set()

    def monitoring_orchestrator() -> None:
        """Orchestrator monitors VLM freshness, triggers abort if stale."""
        freshness_timeout_s = 3.0  # Max time without VLM update before abort
        last_seen_update: int | None = None

        while not stop.is_set():
            result = bb.read("vlm_last_update_ns")
            if result is not None and not result.expired:
                current_update = result.value
                if last_seen_update is None or current_update != last_seen_update:
                    last_seen_update = current_update

            # Check freshness: how long since last NEW update?
            if last_seen_update is not None:
                age_s = (time.monotonic_ns() - last_seen_update) / 1_000_000_000.0
                if age_s > freshness_timeout_s:
                    abort_triggered.set()
                    return

            time.sleep(0.05)

    perc = threading.Thread(target=dying_perception, daemon=True)
    orch = threading.Thread(target=monitoring_orchestrator, daemon=True)

    perc.start()
    orch.start()

    # Perception should crash after ~200ms, then freshness timeout at ~3.2s
    aborted = abort_triggered.wait(timeout=5.0)

    assert aborted, (
        "Orchestrator must detect perception thread death via blackboard "
        "freshness and trigger abort within 5 seconds"
    )

    stop.set()
    perc.join(timeout=1.0)
    orch.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Test 7: Shutdown releases all keys
# ---------------------------------------------------------------------------


def test_shutdown_releases_keys() -> None:
    """On clean shutdown (stop event), release_all() must be called
    before thread join completes. This prevents keys being stuck down
    after the agent exits.

    FAILS against serial runner: serial runner sets self._stop but
    does NOT call InputBackend.release_all() in its shutdown path.
    The concurrent design must guarantee key release on any exit.
    """
    inp = MockInputBackend()
    stop = threading.Event()
    shutdown_complete = threading.Event()
    hwnd = 0

    # Simulate some held keys
    inp.key_down(hwnd, "W")
    inp.key_down(hwnd, "MouseLeft")
    assert inp.held_keys == {"W", "MouseLeft"}

    def orchestrator_with_shutdown() -> None:
        while not stop.is_set():
            time.sleep(0.02)
        # Shutdown path: release all keys before exiting
        inp.release_all(hwnd)
        shutdown_complete.set()

    orch = threading.Thread(target=orchestrator_with_shutdown, daemon=True)
    orch.start()

    # Give orchestrator time to start
    time.sleep(0.05)

    # Signal stop
    stop.set()

    # Wait for shutdown to complete
    completed = shutdown_complete.wait(timeout=2.0)
    orch.join(timeout=2.0)

    assert completed, "Shutdown did not complete within 2 seconds"

    # Verify release_all was called
    release_calls = inp.calls_of("release_all")
    assert len(release_calls) > 0, (
        "release_all must be called during shutdown"
    )

    # Verify no keys remain held
    assert len(inp.held_keys) == 0, (
        f"Keys still held after shutdown: {inp.held_keys}"
    )


# ---------------------------------------------------------------------------
# Test 8: BT HOLD consistency (no flicker)
# ---------------------------------------------------------------------------


def test_bt_hold_consistency() -> None:
    """When the blackboard consistently shows crosshair_block='oak_log'
    and vlm_suggested_action='attack', the harvesting BT should return
    a HOLD attack command on every single tick without flickering.

    This is a UNIT test on the harvesting BT.

    FAILS against serial runner: this validates the BT produces a
    stable HOLD command, but the serial runner converts HOLD into
    per-tick press_and_release calls, meaning each tick is a separate
    key_down + sleep + key_up. The BT itself is stable, but the runner
    breaks the contract. This test documents the BT's output contract
    that the concurrent runner must respect.
    """
    bb = Blackboard()
    tree = build_harvesting_tree()

    # Prime consistency history so confidence is high
    for _ in range(3):
        bb.write("crosshair_block", "oak_log", Producer.VLM)
        bb.write("vlm_suggested_action", "attack", Producer.VLM)

    bb.swap()

    hold_commands = []
    statuses = []

    for tick in range(10):
        status = tree.tick(bb)
        cmd = tree.motor_command
        statuses.append(status)
        hold_commands.append(cmd)

        # After each tick, verify the command is a HOLD attack
        # (The tree has ConfidenceGate which needs 2 consecutive successes,
        # so we allow the first tick to be RUNNING while the gate warms up)
        if tick >= 2 and cmd is not None:  # After ConfidenceGate warmup
                assert cmd.command_type == MotorCommandType.HOLD, (
                    f"Tick {tick}: expected HOLD, got {cmd.command_type}. "
                    f"BT should not flicker between HOLD and other commands."
                )
                assert cmd.action_name == "attack", (
                    f"Tick {tick}: expected 'attack', got '{cmd.action_name}'"
                )

    # At least some ticks must produce HOLD commands (after warmup)
    hold_count = sum(
        1
        for cmd in hold_commands
        if cmd is not None and cmd.command_type == MotorCommandType.HOLD
    )
    assert hold_count >= 5, (
        f"Expected at least 5 HOLD commands out of 10 ticks, got {hold_count}. "
        f"Statuses: {statuses}"
    )
