"""Agent runner v3 — concurrent 2-thread cognitive architecture.

Two-thread design separating perception from orchestration:

    Capture Thread (1Hz) ──► FrameSlot[1] ──► Perception Thread
                              (latest-wins)    ├ Watchdog (frame diff, alerts)
                                               ├ VLM Perception (prompt_builder)
                                               ├ Blackboard write + swap
                                               └ signals Orchestrator via Event
                                                    │
                              Orchestrator Thread (20-50Hz)
                                               ├ reads Blackboard snapshot
                                               ├ FSM (state transitions)
                                               ├ BT[state] (per-tick decisions)
                                               ├ Motor (priority chain → resolve)
                                               ├ stateful key_down/key_up
                                               └ Planner (W1/W2/W5 sparse wake)

Key differences from v2 (serial):
  - Perception runs in its own thread, never blocks orchestrator
  - Orchestrator ticks at 20Hz (50ms) reading Blackboard snapshots
  - Stateful key management (key_down/key_up) instead of blocking holds
  - W2 brain calls dispatched to ThreadPoolExecutor (non-blocking)
  - atexit handler guarantees key release on process exit
"""

from __future__ import annotations

import atexit
import concurrent.futures
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamemind.adapter.schema import Adapter
from gamemind.blackboard import Blackboard, Producer
from gamemind.brain.backend import LLMBackend, LLMResponse
from gamemind.brain.budget_tracker import BudgetExceededError, BudgetTracker
from gamemind.brain.prompt_assembler import (
    assemble_plan_decomposition,
    assemble_replan_from_stuck,
    assemble_task_completion_verification,
    to_messages,
)
from gamemind.bt.engine import Status as BTStatus
from gamemind.bt.harvesting import build_harvesting_tree
from gamemind.bt.motor_command import MotorCommand, MotorCommandType
from gamemind.bt.navigating import build_navigating_tree
from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.events.envelope import make_envelope
from gamemind.events.writer import EventWriter
from gamemind.fsm import FSM, State
from gamemind.input.backend import InputBackend, tap
from gamemind.layer2.action_guard import ActionRepetitionGuard
from gamemind.layer2.stuck_detector import StuckDetector
from gamemind.layer2.wake_trigger import WakeTriggerEvaluator
from gamemind.motor import Motor
from gamemind.perception.freshness import PerceptionResult
from gamemind.perception.prompt_builder import (
    build_tick_messages,
    parse_tick_response,
)
from gamemind.session.manager import SessionManager
from gamemind.session.outcomes import Outcome
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

        self._bt_trees = {
            State.HARVESTING: build_harvesting_tree(),
            State.NAVIGATING: build_navigating_tree(),
        }

        self._brain_call_count = 0
        self._perception_tick_count = 0
        self._subgoals: list[str] = []
        self._current_subgoal_idx = 0
        self._policy_hints: list[str] = []
        self._last_action: str = ""
        self._hallucination_count = 0

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
            target=self._capture_loop, args=(self._frame_slot,),
            name="runner-capture", daemon=True,
        )
        perception_thread = threading.Thread(
            target=self._perception_loop, args=(self._frame_slot,),
            name="runner-perception", daemon=True,
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
                    self._emergency_command = MotorCommand.hold(
                        "backward", duration_ms=500.0
                    )
                    _log(f"WATCHDOG EMERGENCY: {alert.signal}")

            if self._watchdog.is_frozen:
                continue

            try:
                perception = self._run_vlm_perception(cap)
            except Exception as e:  # noqa: BLE001
                _log(f"perception error (skipping frame): {type(e).__name__}: {e}")
                continue
            if perception is None:
                continue
            self._perception_tick_count += 1

            self._bb.write("vlm_last_update_ns", time.monotonic_ns(), Producer.VLM)

            if perception.parsed:
                tick_data = parse_tick_response(
                    perception.parsed,
                    available_actions=self._config.adapter.actions,
                )
                for key, value in tick_data.items():
                    if value is not None:
                        self._bb.write(key, value, Producer.VLM)

                _log(
                    f"  tick #{self._perception_tick_count} "
                    f"block={tick_data.get('crosshair_block', '?')} "
                    f"action={tick_data.get('vlm_suggested_action', '?')} "
                    f"latency={perception.latency_ms:.0f}ms"
                )

            self._bb.swap()
            self._last_perception = perception  # for brain calls
            self._new_perception.set()  # signal orchestrator

    # ------------------------------------------------------------------
    # Orchestrator thread (fast BT decision loop + stateful key mgmt)
    # ------------------------------------------------------------------

    def _orchestrator_loop(self) -> Outcome:
        """Orchestrator: fast BT decision loop with stateful key management."""
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
        # BT decisions only run when NEW perception arrives (1Hz).
        # Between ticks: check safety events, maintain current key state.
        while not self._stop.is_set():
            got_new = self._new_perception.wait(timeout=0.05)
            self._new_perception.clear()

            # Always check freeze/emergency (safety, must be responsive)
            if self._freeze_event.is_set():
                self._release_all_keys()
                self._motor.freeze()
                self._fsm.transition("perception_unavailable")
                continue

            if self._emergency_command is not None:
                self._motor.set_emergency(self._emergency_command)
                self._emergency_command = None

            # Only run BT decisions when NEW perception arrives.
            # Without new data, keep current key state — no re-ticking.
            if not got_new:
                continue

            elapsed_s = (time.monotonic_ns() - start_ns) / 1e9

            perception = self._last_perception
            if perception is None:
                continue

            # Subgoal advancement
            if (
                perception.parsed
                and perception.parsed.get("subgoal_ok") is True
                and self._current_subgoal_idx < len(self._subgoals) - 1
            ):
                self._current_subgoal_idx += 1
                new_sg = self._subgoals[self._current_subgoal_idx]
                self._bb.write("current_subgoal", new_sg, Producer.PLANNER)
                self._bb.swap()
                _log(
                    f"  subgoal advanced → {new_sg} "
                    f"({self._current_subgoal_idx}/{len(self._subgoals)})"
                )

            # Abort checks
            for condition in self._goal.abort_conditions:
                if condition.type == "health_threshold" and self._perception_tick_count < 5:
                    continue
                if check_abort(condition, perception, elapsed_s):
                    _log(f"abort condition fired: {condition.type}")
                    return self._terminate("aborted")

            # Success checks
            success_fired = check_success(self._goal.success_check, perception, elapsed_s)
            if success_fired:
                _log("success predicates fired — calling W5 verify")
                w5_ok = self._call_brain_w5(adapter, config.task, perception)
                if w5_ok:
                    _log("W5 verify passed — session success")
                    return self._terminate("success")
                _log("W5 verify failed — continuing")

            # Wake trigger evaluation
            wake = self._trigger.on_perception_tick(
                perception,
                frame_bytes=b"",  # frame bytes not available in orchestrator
                predicate_fired=success_fired,
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

            # BT tick
            current_bt = self._bt_trees.get(self._fsm.state)
            bt_command: MotorCommand | None = None
            if current_bt is not None:
                bt_status = current_bt.tick(self._bb)
                bt_command = current_bt.motor_command

                if bt_status == BTStatus.SUCCESS and self._fsm.state == State.NAVIGATING:
                    self._fsm.transition("target_reached")
                    _log("  NAVIGATING → HARVESTING (target_reached)")
                    current_bt = self._bt_trees.get(self._fsm.state)
                    if current_bt is not None:
                        current_bt.tick(self._bb)
                        bt_command = current_bt.motor_command
                elif bt_status == BTStatus.SUCCESS and self._fsm.state == State.HARVESTING:
                    vlm_action = self._bb.read_value("vlm_suggested_action")
                    if vlm_action != "attack":
                        self._fsm.transition("resource_exhausted")
                        _log("  HARVESTING → NAVIGATING (resource_exhausted)")
                        current_bt = self._bt_trees.get(self._fsm.state)
                        if current_bt is not None:
                            current_bt.tick(self._bb)
                            bt_command = current_bt.motor_command

            # Hallucination guard
            if bt_command is not None and bt_command.action_name:
                if (
                    bt_command.action_name not in config.adapter.actions
                    and bt_command.action_name != ""
                ):
                    self._hallucination_count += 1
                    _log(f"  hallucination #{self._hallucination_count}: {bt_command.action_name}")
                    self._emit("action", "action_hallucinated", {"action": bt_command.action_name})
                    if self._hallucination_count >= 3:
                        _log("  3 consecutive hallucinations → W2 replan")
                        self._fsm.transition("w2_stuck")
                        self._call_brain_w2(adapter, perception)
                        self._fsm.transition("plan_ready_navigate")
                        self._hallucination_count = 0
                    bt_command = None
                else:
                    self._hallucination_count = 0

            # Motor resolve + stateful key management
            resolved = self._motor.resolve(bt_command)
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
                elif resolved.command_type == MotorCommandType.TAP:
                    _log(f"  key_tap: {resolved.key}")
                    self._release_all_keys()  # release holds before tapping
                    scancodes = tap(resolved.key)
                    config.input.send_scan_codes(config.hwnd, scancodes)

                self._last_action = resolved.action
                self._watchdog.set_motor_moving(
                    resolved.action in ("forward", "backward", "strafe_left", "strafe_right", "attack")
                )
                self._bb.write("last_action", resolved.action, Producer.ACTION)
            else:
                # Release any held keys if no valid command
                if self._held_keys:
                    _log(f"  key_release_all: {self._held_keys} (no valid command)")
                self._release_all_keys()
                self._last_action = ""
                self._watchdog.set_motor_moving(False)

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

    def _call_brain_w5(self, adapter: Adapter, task: str, perception: PerceptionResult) -> bool:
        prompt = assemble_task_completion_verification(
            display_name=adapter.display_name,
            world_facts=adapter.world_facts,
            frame_summary=perception.raw_text or "(no frame summary)",
            success_predicates=str(self._goal.success_check),
            task_description=task,
        )
        resp = self._brain_chat(prompt.system, to_messages(prompt), "w5")
        if resp.parsed_json:
            return bool(resp.parsed_json.get("verify_ok", False))
        return False

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
