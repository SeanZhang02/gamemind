"""Agent runner v5 — Intent-based architecture (spatial perception layer).

Two-thread design separating perception from orchestration:

    Capture Thread (1Hz) ──► FrameSlot[1] ──► Perception Thread
                              (latest-wins)    ├ Watchdog (frame diff, alerts)
                                               ├ VLM Perception (prompt_builder)
                                               ├ SpatialState update + swap
                                               ├ Blackboard write + swap
                                               └ signals Orchestrator via Event
                                                    │
                              Orchestrator Thread (perception-driven)
                                               ├ reads SpatialState snapshot
                                               ├ IntentTracker progress check
                                               ├ IntentExecutor → MotorCommand
                                               ├ Motor (staleness/hysteresis)
                                               ├ stateful key_down/key_up
                                               └ Brain intent decision (non-blocking)

v5 changes from v4:
  - VLM direct drive REMOVED — replaced by intent-based execution
  - SpatialState (double-buffered world model) feeds IntentExecutor
  - IntentTracker monitors progress, triggers Brain calls on stall/complete
  - IntentExecutor (rule engine) maps (intent + spatial) → MotorCommand
  - Camera guard REMOVED — facing correction is handled by IntentExecutor
  - LOG COLLECTED detection and Bug 13 fix preserved
"""

from __future__ import annotations

import atexit
import concurrent.futures
import json
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamemind.adapter.schema import Adapter
from gamemind.blackboard import Blackboard, Producer
from gamemind.brain.backend import LLMBackend, LLMResponse
from gamemind.brain.budget_tracker import BudgetExceededError, BudgetTracker
from gamemind.brain.prompt_assembler import (
    assemble_intent_decision,
    assemble_plan_decomposition,
    assemble_replan_from_stuck,
    to_messages,
)
from gamemind.bt.motor_command import MotorCommand, MotorCommandType
from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.events.envelope import make_envelope
from gamemind.events.writer import EventWriter
from gamemind.fsm import FSM
from gamemind.input.backend import InputBackend, tap
from gamemind.intent.executor import IntentExecutor
from gamemind.intent.models import Intent, IntentStatus, IntentType
from gamemind.intent.tracker import IntentTracker
from gamemind.layer2.action_guard import ActionRepetitionGuard
from gamemind.layer2.stuck_detector import StuckDetector
from gamemind.layer2.wake_trigger import WakeTriggerEvaluator
from gamemind.motor import Motor
from gamemind.perception.freshness import PerceptionResult
from gamemind.perception.prompt_builder import (
    build_tick_messages,
    parse_spatial_response,
    parse_tick_response,
)
from gamemind.session.manager import SessionManager
from gamemind.session.outcomes import Outcome
from gamemind.spatial.state import SpatialPerception, SpatialState
from gamemind.verify.checks import check_abort, check_success
from gamemind.watchdog import AlertLevel, Watchdog


def _log(msg: str) -> None:
    print(f"[gamemind runner] {msg}", flush=True)


@dataclass
class RunnerConfig:
    adapter: Adapter
    task: str
    goal_name: str
    runs_root: Path
    capture: CaptureBackend
    perception: LLMBackend
    brain: LLMBackend
    input: InputBackend | None
    hwnd: int
    budget_usd: float = 0.30
    tick_hz: float | None = None
    dry_run: bool = False

    @property
    def effective_tick_hz(self) -> float:
        return self.tick_hz or self.adapter.perception.tick_hz

    @property
    def freshness_budget_ms(self) -> float:
        return self.adapter.perception.freshness_budget_ms


class FrameSlot:
    """Bounded-size-1 latest-wins slot for CaptureResult."""

    def __init__(self) -> None:
        self._frame: CaptureResult | None = None
        self._cond = threading.Condition()
        self._dropped = 0
        self._closed = False

    def put(self, frame: CaptureResult) -> None:
        with self._cond:
            if self._frame is not None:
                self._dropped += 1
            self._frame = frame
            self._cond.notify_all()

    def take(self, timeout: float = 5.0) -> CaptureResult | None:
        with self._cond:
            deadline = time.monotonic() + timeout
            while self._frame is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(timeout=remaining)
            if self._closed and self._frame is None:
                return None
            frame = self._frame
            self._frame = None
            return frame

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def dropped(self) -> int:
        return self._dropped


class AgentRunner:
    def __init__(
        self,
        config: RunnerConfig,
        session_manager: SessionManager,
        event_writer: EventWriter,
    ) -> None:
        self._config = config
        self._session = session_manager
        self._writer = event_writer
        self._budget = BudgetTracker(config.budget_usd)
        self._stop = threading.Event()

        goal = config.adapter.goal_grammars.get(config.goal_name)
        if goal is None:
            raise ValueError(
                f"goal_name {config.goal_name!r} not in adapter.goal_grammars "
                f"(available: {list(config.adapter.goal_grammars.keys())})"
            )
        self._goal = goal

        self._bb = Blackboard()
        self._watchdog = Watchdog(self._bb)
        self._fsm = FSM()
        self._motor = Motor(config.adapter.actions)

        self._stuck = StuckDetector(stuck_seconds=20.0, entropy_floor=0.02)
        self._guard = ActionRepetitionGuard()
        self._trigger = WakeTriggerEvaluator(stuck=self._stuck, guard=self._guard)

        # Spatial perception layer (v5)
        self._spatial = SpatialState(anchor_max_age_ns=10_000_000_000)
        self._intent_executor = IntentExecutor(config.adapter.actions)
        self._intent_tracker = IntentTracker()
        self._current_intent: Intent | None = None
        self._brain_call_pending = False
        self._perception_fail_count = 0
        self._intent_lock = (
            threading.Lock()
        )  # guards _current_intent + _intent_tracker + _intent_executor

        self._recent_actions: deque[tuple[str, str | None]] = deque(maxlen=5)

        self._brain_call_count = 0
        self._perception_tick_count = 0
        self._subgoals: list[str] = []
        self._current_subgoal_idx = 0
        self._policy_hints: list[str] = []
        self._last_action: str = ""
        self._logs_collected: int = 0
        self._prev_block: str | None = None
        self._attack_on_log_ticks: int = 0

        # Concurrent architecture state
        self._new_perception = threading.Event()
        self._freeze_event = threading.Event()
        self._held_keys: set[str] = set()
        self._last_perception: PerceptionResult | None = None
        self._frame_slot: FrameSlot | None = None
        self._emergency_command: MotorCommand | None = None
        self._brain_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="runner-brain"
        )

        atexit.register(self._atexit_release)

    def run(self) -> Outcome:
        self._frame_slot = FrameSlot()

        capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(self._frame_slot,),
            name="runner-capture",
            daemon=True,
        )
        perception_thread = threading.Thread(
            target=self._perception_loop,
            args=(self._frame_slot,),
            name="runner-perception",
            daemon=True,
        )

        capture_thread.start()
        perception_thread.start()

        try:
            return self._orchestrator_loop()
        finally:
            self._stop.set()
            self._frame_slot.close()
            self._release_all_keys()  # CRITICAL: release before joining
            self._brain_executor.shutdown(wait=False)
            perception_thread.join(timeout=5.0)
            capture_thread.join(timeout=5.0)

    def stop(self) -> None:
        self._stop.set()

    def _capture_loop(self, slot: FrameSlot) -> None:
        tick_interval = 1.0 / self._config.effective_tick_hz
        next_tick = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(0.01, next_tick - now))
                continue
            try:
                result = self._config.capture.capture(hwnd=self._config.hwnd, timeout_ms=1500)
                slot.put(result)
            except Exception as e:  # noqa: BLE001
                _log(f"capture error: {type(e).__name__}: {e}")
            next_tick += tick_interval
            if time.monotonic() - next_tick > tick_interval * 2:
                next_tick = time.monotonic() + tick_interval

    # ------------------------------------------------------------------
    # Perception thread (writes Blackboard, signals orchestrator)
    # ------------------------------------------------------------------

    def _perception_loop(self, slot: FrameSlot) -> None:
        """Perception thread: continuous VLM inference writing to Blackboard."""
        while not self._stop.is_set():
            cap = slot.take(timeout=2.0)
            if cap is None:
                continue

            # Watchdog check (needs frame_bytes, must be in this thread)
            alerts = self._watchdog.check(cap.frame_bytes)
            for alert in alerts:
                if alert.level >= AlertLevel.FATAL:
                    self._freeze_event.set()  # signal orchestrator
                    _log(f"WATCHDOG FATAL: {alert.signal}")
                elif alert.level >= AlertLevel.EMERGENCY:
                    self._emergency_command = MotorCommand.hold("backward", duration_ms=500.0)
                    _log(f"WATCHDOG EMERGENCY: {alert.signal}")

            if self._watchdog.is_frozen:
                continue

            try:
                perception = self._run_vlm_perception(cap)
            except Exception as e:  # noqa: BLE001
                _log(f"perception error (skipping frame): {type(e).__name__}: {e}")
                self._perception_fail_count += 1
                if self._perception_fail_count >= 3:
                    self._freeze_event.set()
                    _log("PERCEPTION UNAVAILABLE: 3 consecutive failures")
                continue
            if perception is None:
                continue
            self._perception_tick_count += 1
            self._perception_fail_count = 0  # reset on success

            self._bb.write("vlm_last_update_ns", time.monotonic_ns(), Producer.VLM)

            if perception.parsed:
                # Legacy Blackboard write (backward compat)
                tick_data = parse_tick_response(
                    perception.parsed,
                    available_actions=self._config.adapter.actions,
                )
                for key, value in tick_data.items():
                    if value is not None:
                        self._bb.write(key, value, Producer.VLM)

                # SpatialState integration — field name mapping
                spatial_data = parse_spatial_response(perception.parsed)
                spatial_perception = SpatialPerception(
                    block=spatial_data.get("crosshair_block"),
                    facing=spatial_data.get("player_facing"),
                    spatial_context=spatial_data.get("spatial_context"),
                    anchors=spatial_data.get("anchors"),
                    health=spatial_data.get("health"),
                    entities=spatial_data.get("entities_nearby"),
                )
                self._spatial.update(spatial_perception)

                # Write spatial fields to BB for orchestrator reads
                if spatial_data.get("player_facing") is not None:
                    self._bb.write("player_facing", spatial_data["player_facing"], Producer.VLM)

                _log(
                    f"  tick #{self._perception_tick_count} "
                    f"block={tick_data.get('crosshair_block', '?')} "
                    f"facing={spatial_data.get('player_facing', '?')} "
                    f"latency={perception.latency_ms:.0f}ms"
                )

            self._bb.swap()
            self._spatial.swap()  # MUST be immediately after bb.swap()
            self._last_perception = perception  # for brain calls
            self._new_perception.set()  # signal orchestrator

    # ------------------------------------------------------------------
    # Orchestrator thread (intent-based execution + stateful key mgmt)
    # ------------------------------------------------------------------

    def _orchestrator_loop(self) -> Outcome:
        """Orchestrator: intent-based execution with stateful key management."""
        config = self._config
        adapter = config.adapter
        start_ns = time.monotonic_ns()

        # Bring game window to foreground before starting input
        if config.hwnd and not config.dry_run:
            self._bring_to_foreground(config.hwnd)

        # --- W1 brain call (blocking is OK — happens once at start) ---
        self._fsm.transition("session_start")

        assert self._frame_slot is not None  # noqa: S101
        first_cap = self._frame_slot.take(timeout=5.0)
        first_perception = None
        if first_cap and not config.dry_run:
            first_perception = self._run_vlm_perception(first_cap)

        w1_response = self._call_brain_w1(adapter, config.task, first_perception)
        if w1_response.parsed_json:
            self._subgoals = w1_response.parsed_json.get("subgoals", [])
            self._policy_hints = w1_response.parsed_json.get("policy_hints", [])
            _log(f"W1 subgoals: {self._subgoals}")
            _log(f"W1 hints: {self._policy_hints}")

        if self._subgoals:
            self._bb.write("current_subgoal", self._subgoals[0], Producer.PLANNER)
            self._bb.write("plan_sequence", self._subgoals, Producer.PLANNER)

        self._fsm.transition("plan_ready_navigate")

        # --- Main orchestrator loop ---
        # Intent-based execution: IntentTracker monitors progress,
        # IntentExecutor produces MotorCommands from (intent + spatial state).
        while not self._stop.is_set():
            got_new = self._new_perception.wait(timeout=0.05)
            self._new_perception.clear()

            # Always check freeze/emergency (safety, must be responsive)
            if self._freeze_event.is_set():
                # Check if freeze condition has cleared (watchdog recovered or perception resumed)
                if not self._watchdog.is_frozen and self._perception_fail_count < 3:
                    self._freeze_event.clear()
                    self._motor.unfreeze()
                    _log("freeze recovered — resuming")
                else:
                    self._release_all_keys()
                    self._motor.freeze()
                    self._fsm.transition("perception_unavailable")
                    continue

            if self._emergency_command is not None:
                self._motor.set_emergency(self._emergency_command)
                self._emergency_command = None

            # Only act on NEW perception data.
            # Without new data, keep current key state — no re-ticking.
            if not got_new:
                continue

            elapsed_s = (time.monotonic_ns() - start_ns) / 1e9

            perception = self._last_perception
            if perception is None:
                continue

            # Read spatial data from Blackboard (also available via SpatialState)
            crosshair_block = self._bb.read_value("crosshair_block")
            health_value = self._bb.read_value("health")
            facing_value = self._bb.read_value("player_facing")

            # Extract facing from spatial snapshot if BB doesn't have it
            if facing_value is None:
                snap = self._spatial.snapshot()
                if snap and "Camera:" in snap:
                    # Parse "Camera: looking at horizon." → "looking_at_horizon"
                    camera_part = snap.split("Camera:")[1].split(".")[0].strip()
                    facing_value = camera_part.replace(" ", "_")

            # Block-break event counting (hardened log collection — Bug 13 fix preserved)
            # FIRST: check collection (uses counter from previous ticks)
            current_block = crosshair_block
            # Determine current action for log counting
            current_action_name = ""
            if (
                self._current_intent
                and self._current_intent.intent_type == IntentType.ATTACK_TARGET
            ):
                current_action_name = "attack"

            if (
                self._prev_block
                and "log" in self._prev_block.lower()
                and (current_block is None or "log" not in current_block.lower())
                and self._attack_on_log_ticks >= 3
            ):
                self._logs_collected += 1
                self._attack_on_log_ticks = 0
                _log(f"  LOG COLLECTED (attack-verified)! total={self._logs_collected}")

            # SECOND: update counter for THIS tick
            if current_action_name == "attack" and current_block and "log" in current_block.lower():
                self._attack_on_log_ticks += 1
            else:
                self._attack_on_log_ticks = 0

            self._prev_block = current_block

            # Abort checks
            for condition in self._goal.abort_conditions:
                if condition.type == "health_threshold" and self._perception_tick_count < 5:
                    continue
                if check_abort(condition, perception, elapsed_s):
                    _log(f"abort condition fired: {condition.type}")
                    return self._terminate("aborted")

            # Success checks — inject block-break log count as synthetic inventory
            if (
                perception
                and perception.parsed is not None
                and "inventory" not in perception.parsed
                and self._logs_collected > 0
            ):
                perception_with_inventory = PerceptionResult(
                    frame_id=perception.frame_id,
                    capture_ts_monotonic_ns=perception.capture_ts_monotonic_ns,
                    frame_age_ms=perception.frame_age_ms,
                    parsed={**perception.parsed, "inventory": {"log": self._logs_collected}},
                    raw_text=perception.raw_text,
                    latency_ms=perception.latency_ms,
                )
            else:
                perception_with_inventory = perception
            success_fired = check_success(
                self._goal.success_check, perception_with_inventory, elapsed_s
            )
            if success_fired:
                _log(
                    f"success predicates fired — task complete "
                    f"(event-verified, logs={self._logs_collected})"
                )
                return self._terminate("success")

            # Wake trigger evaluation (W2 stuck detection)
            # Suppress W2 when actively attacking a log (productive work)
            wake = self._trigger.on_perception_tick(
                perception,
                frame_bytes=b"",
                predicate_fired=success_fired
                or (
                    current_action_name == "attack"
                    and current_block is not None
                    and "log" in current_block.lower()
                ),
                action_executed=self._last_action != "",
                last_action_hash=self._last_action,
                abort_triggered=False,
                ts_ns=time.monotonic_ns(),
            )

            if wake.reason == "w2_stuck":
                _log(f"W2 stuck: {wake.payload}")
                self._fsm.transition("w2_stuck")
                self._call_brain_w2(adapter, perception)
                self._fsm.transition("plan_ready_navigate")

            # Runaway check
            if self._brain_call_count >= 30:
                _log("brain call count exceeded 30 — runaway")
                return self._terminate("runaway")

            # --- Intent-based execution (thread-safe via _intent_lock) ---
            # Look up target anchor info from SpatialState directly (not text parsing)
            target_dir: str | None = None
            target_dist: str | None = None
            with self._intent_lock:
                current_intent = self._current_intent  # snapshot under lock

            if current_intent and current_intent.target_anchor:
                anchor = self._spatial.get_anchor(current_intent.target_anchor)
                if anchor is not None:
                    target_dir = anchor.direction
                    target_dist = anchor.distance

            # Coerce health to float for IntentTracker comparison
            health_float: float | None = None
            if health_value is not None:
                try:
                    health_float = float(health_value)
                except (ValueError, TypeError):
                    health_float = None

            # Check intent progress
            intent_command: MotorCommand | None = None
            with self._intent_lock:
                current_intent = self._current_intent
                if current_intent:
                    intent_status = self._intent_tracker.check_progress(
                        crosshair_block=crosshair_block,
                        target_anchor_direction=target_dir,
                        target_anchor_distance=target_dist,
                        facing=facing_value,
                        health=health_float,
                    )

                    if intent_status in (
                        IntentStatus.COMPLETED,
                        IntentStatus.STALLED,
                        IntentStatus.BLOCKED,
                    ):
                        # Advance subgoal on COMPLETED
                        if (
                            intent_status == IntentStatus.COMPLETED
                            and self._current_subgoal_idx < len(self._subgoals) - 1
                        ):
                            self._current_subgoal_idx += 1
                            _log(
                                f"  subgoal advanced → {self._subgoals[self._current_subgoal_idx]} "
                                f"({self._current_subgoal_idx}/{len(self._subgoals)})"
                            )

                        _log(
                            f"  intent {current_intent.intent_type.value} "
                            f"status={intent_status.value}"
                        )
                        if not self._brain_call_pending:
                            self._brain_call_pending = True
                            self._brain_executor.submit(
                                self._call_brain_intent_decision, intent_status
                            )

                    # Execute current intent action (even during brain call — non-blocking)
                    intent_command = self._intent_executor.next_action(
                        current_intent,
                        spatial_snapshot=self._spatial.snapshot(),
                        crosshair_block=crosshair_block,
                        facing=facing_value,
                        anchor_direction=target_dir,
                    )
                    _log(
                        f"  intent_exec: {current_intent.intent_type.value} "
                        f"→ {intent_command.action_name or 'idle'} "
                        f"({intent_command.command_type.name})"
                    )
                else:
                    # No current intent — request initial Brain intent decision
                    if not self._brain_call_pending and self._subgoals:
                        self._brain_call_pending = True
                        self._brain_executor.submit(
                            self._call_brain_intent_decision, IntentStatus.IDLE
                        )

            # Motor resolve + stateful key management
            resolved = self._motor.resolve(intent_command)
            if (
                resolved is not None
                and resolved.key
                and config.input is not None
                and not config.dry_run
            ):
                current_key = resolved.key
                if resolved.command_type == MotorCommandType.HOLD:
                    # Release any OTHER held keys before holding the new one
                    for k in list(self._held_keys):
                        if k != current_key:
                            _log(f"  key_switch: releasing {k} for {current_key}")
                            config.input.key_up(config.hwnd, k)
                            self._held_keys.discard(k)
                    if current_key not in self._held_keys:
                        _log(f"  key_hold: pressing {current_key}")
                        config.input.key_down(config.hwnd, current_key)
                        self._held_keys.add(current_key)
                elif resolved.command_type == MotorCommandType.MOUSE_MOVE:
                    self._release_all_keys()  # stop attacking while moving camera
                    _log(f"  mouse_move: dx={resolved.dx} dy={resolved.dy}")
                    config.input.mouse_move_rel(config.hwnd, resolved.dx, resolved.dy)
                elif resolved.command_type == MotorCommandType.TAP:
                    _log(f"  key_tap: {resolved.key}")
                    self._release_all_keys()  # release holds before tapping
                    scancodes = tap(resolved.key)
                    config.input.send_scan_codes(config.hwnd, scancodes)

                self._last_action = resolved.action
                self._watchdog.set_motor_moving(
                    resolved.action
                    in ("forward", "backward", "strafe_left", "strafe_right", "attack")
                )
                self._bb.write("last_action", resolved.action, Producer.ACTION)
            else:
                # Release any held keys if no valid command
                if self._held_keys:
                    _log(f"  key_release_all: {self._held_keys} (no valid command)")
                self._release_all_keys()
                self._last_action = ""
                self._watchdog.set_motor_moving(False)

            # Record action to history for VLM temporal context
            action_name = intent_command.action_name if intent_command else "none"
            self._recent_actions.append((action_name, current_block))

        return self._terminate("user_stopped")

    def _run_vlm_perception(self, cap: CaptureResult) -> PerceptionResult | None:
        capture_ts_ns = time.monotonic_ns() - int(cap.frame_age_ms * 1_000_000)
        frame_id = uuid.uuid4().hex[:12]
        current_subgoal = (
            self._subgoals[self._current_subgoal_idx]
            if self._current_subgoal_idx < len(self._subgoals)
            else "observe"
        )

        if self._config.dry_run:
            resp = self._config.perception.chat(
                messages=[{"role": "user", "content": "dry-run perception tick"}],
                temperature=0.0,
                max_tokens=512,
                cache_system=False,
                request_id=f"perception-{frame_id}",
                emit_event=False,
            )
        else:
            sys_prompt, messages = build_tick_messages(
                frame_bytes=cap.frame_bytes,
                current_subgoal=current_subgoal,
                policy_hints=self._policy_hints,
                available_actions=self._config.adapter.actions,
                last_action=self._last_action,
                recent_actions=list(self._recent_actions),
                last_frame_diff=self._spatial.diff(),
            )
            full_messages = [{"role": "system", "content": sys_prompt}, *messages]
            resp = self._config.perception.chat(
                messages=full_messages,
                temperature=0.0,
                max_tokens=512,
                cache_system=False,
                request_id=f"perception-{frame_id}",
                emit_event=False,
            )

        return PerceptionResult(
            frame_id=frame_id,
            capture_ts_monotonic_ns=capture_ts_ns,
            frame_age_ms=cap.frame_age_ms,
            parsed=resp.parsed_json,
            raw_text=resp.text,
            latency_ms=resp.latency_ms,
        )

    def _call_brain_w1(
        self, adapter: Adapter, task: str, perception: PerceptionResult | None
    ) -> LLMResponse:
        frame_summary = perception.raw_text if perception else "(initial observation pending)"
        prompt = assemble_plan_decomposition(
            display_name=adapter.display_name,
            actions=adapter.actions,
            world_facts=adapter.world_facts,
            task_description=task,
            frame_summary=frame_summary,
            success_check=str(self._goal.success_check),
            abort_conditions=str(self._goal.abort_conditions),
        )
        return self._brain_chat(prompt.system, to_messages(prompt), "w1")

    def _call_brain_w2(self, adapter: Adapter, perception: PerceptionResult) -> None:
        """Dispatch W2 replan to background thread (non-blocking)."""
        # Capture values for the closure — perception may change by the
        # time the background thread runs.
        raw_text = perception.raw_text or "(no frame summary)"
        last_action = self._last_action or "(none)"
        current_plan = json.dumps(self._subgoals) if self._subgoals else "(no plan)"

        def _w2_task() -> None:
            prompt = assemble_replan_from_stuck(
                display_name=adapter.display_name,
                world_facts=adapter.world_facts,
                frame_summary=raw_text,
                recent_actions=last_action,
                current_plan=current_plan,
                stuck_seconds=20.0,
            )
            resp = self._brain_chat(prompt.system, to_messages(prompt), "w2")
            if resp.parsed_json:
                if "subgoals" in resp.parsed_json:
                    self._subgoals = resp.parsed_json["subgoals"]
                    self._current_subgoal_idx = 0
                    _log(f"W2 replan subgoals: {self._subgoals}")
                if "policy_hints" in resp.parsed_json:
                    self._policy_hints = resp.parsed_json["policy_hints"]
                if self._subgoals:
                    self._bb.write("current_subgoal", self._subgoals[0], Producer.PLANNER)
                    self._bb.swap()

        self._brain_executor.submit(_w2_task)

    def _call_brain_intent_decision(self, trigger_status: IntentStatus) -> None:
        """Background thread: ask Claude for next intent."""
        try:
            spatial_snap = self._spatial.snapshot()
            current_subgoal = (
                self._subgoals[self._current_subgoal_idx]
                if self._current_subgoal_idx < len(self._subgoals)
                else "observe"
            )

            last_intents_text = "none"  # TODO: track last N intents for context
            trigger_text = f"Previous intent {trigger_status.value}"
            with self._intent_lock:
                if self._current_intent:
                    trigger_text += (
                        f": {self._current_intent.intent_type.value}"
                        f" targeting {self._current_intent.target_anchor}"
                    )

            available_intents = ", ".join(t.value for t in IntentType)

            prompt = assemble_intent_decision(
                display_name=self._config.adapter.display_name,
                world_facts=self._config.adapter.world_facts,
                spatial_snapshot=spatial_snap,
                current_subgoal=current_subgoal,
                last_intents=last_intents_text,
                available_intents=available_intents,
                trigger_reason=trigger_text,
            )

            resp = self._brain_chat(prompt.system, to_messages(prompt), "intent")

            if resp.parsed_json:
                intent_type_str = resp.parsed_json.get("intent", "look_around")
                try:
                    intent_type = IntentType(intent_type_str)
                except ValueError:
                    intent_type = IntentType.LOOK_AROUND  # fallback

                # Clamp max_steps to sane range [5, 30] to prevent infinite loops
                raw_max_steps = resp.parsed_json.get("max_steps", 20)
                try:
                    max_steps = max(5, min(30, int(raw_max_steps)))
                except (ValueError, TypeError):
                    max_steps = 20

                # Normalize target_anchor for case-insensitive matching
                target_anchor = resp.parsed_json.get("target_anchor")
                if target_anchor and isinstance(target_anchor, str):
                    target_anchor = target_anchor.strip().lower()

                new_intent = Intent(
                    intent_type=intent_type,
                    target_anchor=target_anchor,
                    expected_outcome=resp.parsed_json.get("expected_outcome", ""),
                    max_steps=max_steps,
                    reason=resp.parsed_json.get("reason", ""),
                )
            else:
                # Brain returned no valid JSON — fallback to look_around
                _log("  brain returned no valid JSON — fallback to look_around")
                new_intent = Intent(
                    intent_type=IntentType.LOOK_AROUND,
                    expected_outcome="survey surroundings after brain parse failure",
                    max_steps=15,
                    reason="brain_parse_failure_fallback",
                )

            # Atomic intent state update — order matters (tracker+executor ready before intent visible)
            with self._intent_lock:
                self._intent_tracker.start(new_intent)
                self._intent_executor.reset()
                self._current_intent = new_intent  # LAST: make visible to orchestrator
            _log(f"  new intent: {new_intent.intent_type.value} → {new_intent.target_anchor}")
        finally:
            self._brain_call_pending = False

    def _brain_chat(self, system: str, messages: list[dict[str, Any]], trigger: str) -> LLMResponse:
        self._brain_call_count += 1
        request_id = f"{trigger}-{uuid.uuid4().hex[:8]}"
        _log(f"brain call #{self._brain_call_count} ({trigger}) request_id={request_id}")
        self._emit("brain", f"wake_{trigger}", {"request_id": request_id})
        resp = self._config.brain.chat(
            messages=messages,
            temperature=1.0,
            max_tokens=1024,
            cache_system=True,
            request_id=request_id,
        )
        _log(
            f"  response: {resp.latency_ms:.0f}ms cost=${resp.cost_estimate_usd:.6f} tokens={resp.prompt_tokens}+{resp.completion_tokens}"
        )
        try:
            self._budget.record(resp.cost_estimate_usd)
        except BudgetExceededError as e:
            _log(f"  BUDGET EXCEEDED: {e}")
            raise
        return resp

    @staticmethod
    def _bring_to_foreground(hwnd: int) -> None:
        """Bring the game window to foreground before starting input."""
        try:
            import ctypes  # noqa: PLC0415

            user32 = ctypes.windll.user32
            user32.SetForegroundWindow(hwnd)
            _log(f"brought HWND {hwnd} to foreground")
            time.sleep(0.3)  # brief pause for window manager to settle
        except Exception as e:  # noqa: BLE001
            _log(f"SetForegroundWindow failed (non-fatal): {e}")

    def _release_all_keys(self) -> None:
        """Release all physically held keys. Called on freeze/shutdown/idle."""
        if self._config.input is not None and self._held_keys:
            self._config.input.release_all(self._config.hwnd)
        self._held_keys.clear()

    def _atexit_release(self) -> None:
        """Emergency key release on process exit."""
        try:
            self._release_all_keys()
        except Exception:  # noqa: BLE001
            pass

    def _emit(self, producer: str, event_type: str, payload: dict[str, Any]) -> None:
        info = self._session.snapshot()
        if info.session_id is None:
            return
        envelope = make_envelope(
            session_id=info.session_id,
            producer=producer,
            event_type=event_type,
            payload=payload,
        )
        self._writer.write(envelope)

    def _terminate(self, outcome: Outcome) -> Outcome:
        _log(f"session terminal: outcome={outcome}")
        _log(f"budget: ${self._budget.total_usd:.6f} / ${self._config.budget_usd:.2f}")
        _log(
            f"brain calls: {self._brain_call_count}, perception ticks: {self._perception_tick_count}"
        )
        return outcome
