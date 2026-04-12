"""Agent runner — ties all layers into a single execution loop.

Architecture (Batch B spike transplant):

    Capture Thread (2Hz) ──► FrameSlot[1] ──► Agent Thread
                              (latest-wins)    ├ perception (Ollama)
                                               ├ verify (predicates)
                                               ├ layer2 (stuck/guard/wake)
                                               ├ brain (Anthropic, on wake)
                                               └ input (pydirectinput)

FrameSlot implements §1.1.A bounded-size-1 latest-wins queue at the
capture→agent boundary. Perception runs synchronously in the agent
thread, so there's no separate PerceptionResult queue — the agent loop
processes each perception result immediately.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamemind.adapter.schema import Adapter
from gamemind.brain.backend import LLMBackend, LLMResponse
from gamemind.brain.budget_tracker import BudgetExceededError, BudgetTracker
from gamemind.brain.prompt_assembler import (
    assemble_plan_decomposition,
    assemble_replan_from_stuck,
    assemble_task_completion_verification,
    to_messages,
)
from gamemind.capture.backend import CaptureBackend, CaptureResult
from gamemind.input.backend import InputBackend, ScanCode, press_and_release, tap
from gamemind.events.envelope import make_envelope
from gamemind.events.writer import EventWriter
from gamemind.layer2.action_guard import ActionRepetitionGuard
from gamemind.layer2.stuck_detector import StuckDetector
from gamemind.layer2.wake_trigger import WakeTriggerEvaluator
from gamemind.perception.freshness import PerceptionResult, is_stale
from gamemind.session.manager import SessionManager
from gamemind.session.outcomes import Outcome
from gamemind.verify.checks import check_abort, check_success

PERCEPTION_PROMPT = (
    "You are observing a Minecraft first-person screenshot. Report what you see as JSON.\n"
    "Include these fields:\n"
    '  "block": the block type at the crosshair center (oak_log, stone, grass_block, air, etc)\n'
    '  "inventory": {item_id: count} for items visible in the hotbar\n'
    '  "health": float 0-1 (1.0 = full hearts) estimated from health bar\n'
    '  "entities": list of visible entity types nearby\n'
    "Respond with ONLY valid JSON. No prose."
)


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
    """Bounded-size-1 latest-wins slot for CaptureResult.

    Same semantics as gamemind/perception/freshness.py::FreshnessQueue but
    typed for CaptureResult at the capture→agent boundary. Includes
    Condition-based blocking take() (vs FreshnessQueue's polling take).
    """

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
        self._stuck = StuckDetector(
            stuck_seconds=20.0,
            entropy_floor=0.02,
        )
        self._guard = ActionRepetitionGuard()
        self._trigger = WakeTriggerEvaluator(
            stuck=self._stuck,
            guard=self._guard,
        )
        self._current_plan: str = ""
        self._last_action_hash: str | None = None
        self._brain_call_count = 0
        self._pending_actions: list[dict[str, Any]] = []

    def run(self) -> Outcome:
        slot = FrameSlot()
        capture_thread = threading.Thread(
            target=self._capture_loop,
            args=(slot,),
            name="runner-capture",
            daemon=True,
        )
        capture_thread.start()
        try:
            return self._agent_loop(slot)
        finally:
            self._stop.set()
            slot.close()
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

    def _agent_loop(self, slot: FrameSlot) -> Outcome:
        config = self._config
        adapter = config.adapter
        start_ns = time.monotonic_ns()

        wake = self._trigger.on_session_start(
            task=config.task,
            adapter_name=adapter.display_name,
        )
        self._emit("layer2", "stuck_detected", {"trigger": "w1_task_start"})

        w1_response = self._call_brain_w1(adapter, config.task)
        if w1_response.parsed_json:
            if "plan" in w1_response.parsed_json:
                self._current_plan = json.dumps(w1_response.parsed_json["plan"])
                _log(f"W1 plan: {self._current_plan}")
            if "actions" in w1_response.parsed_json:
                self._pending_actions = w1_response.parsed_json["actions"]
                _log(f"W1 queued {len(self._pending_actions)} actions")

        last_action_executed = False

        while not self._stop.is_set():
            cap = slot.take(timeout=2.0)
            if cap is None:
                continue

            elapsed_s = (time.monotonic_ns() - start_ns) / 1_000_000_000.0

            for condition in self._goal.abort_conditions:
                if check_abort(condition, None, elapsed_s):
                    _log(f"abort condition fired: {condition.type}")
                    return self._terminate("aborted")

            perception = self._run_perception(cap)
            if perception is None:
                continue

            if is_stale(perception, budget_ms=config.freshness_budget_ms):
                self._emit(
                    "perception",
                    "perception_stale_dropped",
                    {
                        "frame_age_ms": perception.age_now_ms(),
                    },
                )
                continue

            for condition in self._goal.abort_conditions:
                if check_abort(condition, perception, elapsed_s):
                    _log(f"abort condition fired: {condition.type}")
                    return self._terminate("aborted")

            success_fired = check_success(self._goal.success_check, perception, elapsed_s)

            if success_fired:
                _log("success predicates fired — calling W5 verify")
                w5_ok = self._call_brain_w5(adapter, config.task, perception)
                if w5_ok:
                    _log("W5 verify passed — session success")
                    return self._terminate("success")
                _log("W5 verify failed — continuing")

            wake = self._trigger.on_perception_tick(
                perception,
                frame_bytes=cap.frame_bytes,
                predicate_fired=success_fired,
                action_executed=last_action_executed,
                last_action_hash=self._last_action_hash,
                abort_triggered=False,
                ts_ns=time.monotonic_ns(),
            )

            if wake.reason == "w2_stuck":
                _log(f"W2 stuck trigger: {wake.payload}")
                self._emit("layer2", "stuck_detected", wake.payload)
                self._call_brain_w2(adapter, perception)

            if self._brain_call_count >= 30:
                _log("brain call count exceeded 30 — runaway abort")
                return self._terminate("runaway")

            last_action_executed = self._execute_next_action()

        return self._terminate("user_stopped")

    def _run_perception(self, cap: CaptureResult) -> PerceptionResult | None:
        capture_ts_ns = time.monotonic_ns() - int(cap.frame_age_ms * 1_000_000)
        frame_id = uuid.uuid4().hex[:12]

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
            img_b64 = base64.b64encode(cap.frame_bytes).decode("ascii")
            resp = self._config.perception.chat(
                messages=[
                    {
                        "role": "user",
                        "content": PERCEPTION_PROMPT,
                        "images": [img_b64],
                    }
                ],
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

    def _execute_next_action(self) -> bool:
        """Pop and execute the next pending action from the brain's queue.

        Returns True if an action was executed, False if queue is empty
        or input backend is unavailable. Maps brain action names through
        adapter.actions to key bindings, then sends via InputBackend.
        """
        if not self._pending_actions:
            return False
        if self._config.input is None or self._config.dry_run:
            if self._pending_actions:
                action = self._pending_actions.pop(0)
                _log(f"  action (dry-run skip): {action}")
            return False

        action = self._pending_actions.pop(0)
        scancodes = self._resolve_action(action)
        if not scancodes:
            _log(f"  action unresolvable: {action}")
            return False

        result = self._config.input.send_scan_codes(self._config.hwnd, scancodes)
        action_hash = hashlib.sha256(
            "|".join(f"{c.key}:{int(c.down)}:{c.hold_ms:.1f}" for c in scancodes).encode()
        ).hexdigest()[:16]
        self._last_action_hash = action_hash

        self._emit(
            "action",
            "action_executed" if result.executed else "action_dropped_focus",
            {
                "action": action,
                "action_hash": action_hash,
                "executed": result.executed,
                "dropped_reason": result.dropped_reason,
                "latency_ms": result.latency_ms,
            },
        )
        _log(
            f"  action: {action.get('action', '?')} "
            f"executed={result.executed} "
            f"latency={result.latency_ms:.0f}ms"
        )
        return result.executed

    def _resolve_action(self, action: dict[str, Any]) -> list[ScanCode]:
        """Map a brain action dict to InputBackend scan codes.

        Brain returns actions like:
          {"action": "forward", "duration_ms": 2000}
          {"action": "attack", "hold": true}
          {"action": "forward"}  (tap)

        Adapter maps action names to key bindings:
          actions: {forward: "W", attack: "MouseLeft", ...}
        """
        action_name = action.get("action", "")
        adapter_actions = self._config.adapter.actions
        key = adapter_actions.get(action_name)
        if key is None:
            return []
        duration_ms = action.get("duration_ms", 0)
        if duration_ms > 0:
            return press_and_release(key, hold_ms=float(duration_ms))
        return tap(key)

    def _call_brain_w1(self, adapter: Adapter, task: str) -> LLMResponse:
        prompt = assemble_plan_decomposition(
            display_name=adapter.display_name,
            actions=adapter.actions,
            world_facts=adapter.world_facts,
            task_description=task,
            frame_summary="(initial frame — no observation yet)",
            success_check=str(self._goal.success_check),
            abort_conditions=str(self._goal.abort_conditions),
        )
        return self._brain_chat(prompt.system, to_messages(prompt), "w1")

    def _call_brain_w2(self, adapter: Adapter, perception: PerceptionResult) -> None:
        prompt = assemble_replan_from_stuck(
            display_name=adapter.display_name,
            world_facts=adapter.world_facts,
            frame_summary=perception.raw_text or "(no frame summary)",
            recent_actions="(action history TBD)",
            current_plan=self._current_plan or "(no plan)",
            stuck_seconds=20.0,
        )
        resp = self._brain_chat(prompt.system, to_messages(prompt), "w2")
        if resp.parsed_json and "plan" in resp.parsed_json:
            self._current_plan = json.dumps(resp.parsed_json["plan"])
            _log(f"W2 replan: {self._current_plan}")

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
            f"  response: {resp.latency_ms:.0f}ms "
            f"cost=${resp.cost_estimate_usd:.6f} "
            f"tokens={resp.prompt_tokens}+{resp.completion_tokens}"
        )

        try:
            self._budget.record(resp.cost_estimate_usd)
        except BudgetExceededError as e:
            _log(f"  BUDGET EXCEEDED: {e}")
            raise

        self._emit(
            "brain",
            "brain_response_ok",
            {
                "request_id": request_id,
                "latency_ms": resp.latency_ms,
                "cost_usd": resp.cost_estimate_usd,
                "tokens_in": resp.prompt_tokens,
                "tokens_out": resp.completion_tokens,
            },
        )

        return resp

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
        _log(f"brain calls: {self._brain_call_count}")
        return outcome
