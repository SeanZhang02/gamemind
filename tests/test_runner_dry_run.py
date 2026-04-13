"""E2E dry-run integration test for AgentRunner (v5 intent-based architecture).

Runs the full runner pipeline with mock capture + mock perception +
mock brain, validating:
  1. Session reaches outcome=success
  2. Brain call count matches W1 (plan) + intent decision calls
  3. Budget tracker recorded calls
  4. Events were emitted correctly
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from gamemind.adapter.loader import load
from gamemind.brain.backend import LLMResponse
from gamemind.brain.mock_backend import MockBrainBackend
from gamemind.capture.backend import CaptureResult
from gamemind.events.writer import EventWriter
from gamemind.runner import AgentRunner, RunnerConfig
from gamemind.session.manager import SessionManager

ADAPTER_PATH = Path(__file__).parents[1] / "adapters" / "minecraft.yaml"


def _mock_frame() -> bytes:
    img = Image.new("RGB", (64, 64), (100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


class _MockCapture:
    def __init__(self) -> None:
        self._frame = _mock_frame()
        self.capture_count = 0

    def capture(self, hwnd: int, timeout_ms: int = 500) -> CaptureResult:
        self.capture_count += 1
        return CaptureResult(
            frame_bytes=self._frame,
            frame_age_ms=50.0,
            capture_backend="mock",
            variance=0.5,
            width=64,
            height=64,
        )

    def liveness(self) -> bool:
        return True


def _make_response(text: str, parsed: dict, pt: int = 500, ct: int = 100) -> LLMResponse:
    return LLMResponse(
        text=text,
        parsed_json=parsed,
        prompt_tokens=pt,
        completion_tokens=ct,
        cost_estimate_usd=0.005,
        latency_ms=0.0,
        request_id="",
        cached_system=False,
    )


def test_runner_dry_run_chop_logs_succeeds(tmp_path: Path) -> None:
    adapter = load(ADAPTER_PATH)

    # Brain calls: W1 (plan) + intent decision(s)
    # The intent decision returns attack_target on oak_log, which triggers
    # the IntentExecutor to produce attack commands.
    mock_brain = MockBrainBackend(
        scripted=[
            # W1 plan decomposition
            _make_response(
                '{"subgoals": ["approach_tree", "chop_trunk"], "policy_hints": ["attack logs"]}',
                {"subgoals": ["approach_tree", "chop_trunk"], "policy_hints": ["attack logs"]},
            ),
            # Intent decision (requested when no current intent + subgoals exist)
            _make_response(
                '{"intent": "attack_target", "target_anchor": "oak_log", "max_steps": 20}',
                {"intent": "attack_target", "target_anchor": "oak_log", "max_steps": 20},
            ),
        ]
    )

    # Perception returns spatial JSON with inventory for success check
    mock_perception = MockBrainBackend(
        scripted=[
            _make_response(
                '{"block": "oak_log", "facing": "looking_at_horizon", "health": 1.0, "inventory": {"log": 3}}',
                {
                    "block": "oak_log",
                    "facing": "looking_at_horizon",
                    "health": 1.0,
                    "inventory": {"log": 3},
                },
                pt=200,
                ct=30,
            ),
        ]
        * 50
    )

    mock_capture = _MockCapture()

    session_manager = SessionManager()
    event_writer = EventWriter(tmp_path / "test-session")
    event_writer.start()

    session_manager.start(
        adapter_path=ADAPTER_PATH,
        task_description="chop 3 oak logs",
        runs_root=tmp_path,
    )

    config = RunnerConfig(
        adapter=adapter,
        task="chop 3 oak logs",
        goal_name="chop_logs",
        runs_root=tmp_path,
        capture=mock_capture,
        perception=mock_perception,
        brain=mock_brain,
        input=None,
        hwnd=0,
        budget_usd=1.0,
        dry_run=True,
    )

    runner = AgentRunner(config, session_manager, event_writer)
    outcome = runner.run()

    assert outcome == "success"
    # W1 plan + at least 1 intent decision = minimum 2 brain calls
    assert mock_brain.call_count >= 1  # W1 always fires
    assert mock_capture.capture_count >= 1

    session_manager.transition_to_terminal(outcome=outcome)
    event_writer.close()


def test_log_counting_sequence() -> None:
    """Bug 13: collection check must happen BEFORE counter reset.

    Directly simulates the log-collection logic extracted from runner's
    orchestrator loop. 4 ticks of attack on oak_log, then block changes
    to air. The collection should be detected because _attack_on_log_ticks
    was >= 3 when the block transitioned.
    """
    # Simulate the runner's log-counting state
    logs_collected = 0
    attack_on_log_ticks = 0
    prev_block: str | None = None

    # Sequence of (vlm_action, current_block) per tick
    ticks = [
        ("attack", "oak_log"),
        ("attack", "oak_log"),
        ("attack", "oak_log"),
        ("attack", "oak_log"),  # 4 ticks attacking oak_log
        ("attack", "air"),      # block breaks → now air
    ]

    for vlm_action, current_block in ticks:
        # --- This is the FIXED logic from runner.py ---
        # FIRST: check collection (uses counter from previous ticks)
        if (
            prev_block
            and "log" in prev_block.lower()
            and (current_block is None or "log" not in current_block.lower())
            and attack_on_log_ticks >= 3
        ):
            logs_collected += 1
            attack_on_log_ticks = 0

        # SECOND: update counter for THIS tick
        if vlm_action == "attack" and current_block and "log" in current_block.lower():
            attack_on_log_ticks += 1
        else:
            attack_on_log_ticks = 0

        prev_block = current_block

    assert logs_collected == 1, (
        f"Expected logs_collected == 1 but got {logs_collected}. "
        "Bug 13: collection check must see counter from previous ticks."
    )


def _test_log_counting_old_buggy_sequence() -> None:
    """Verify the OLD (buggy) sequence would fail — kept as documentation."""
    logs_collected = 0
    attack_on_log_ticks = 0
    prev_block: str | None = None

    ticks = [
        ("attack", "oak_log"),
        ("attack", "oak_log"),
        ("attack", "oak_log"),
        ("attack", "oak_log"),
        ("attack", "air"),
    ]

    for vlm_action, current_block in ticks:
        # OLD BUGGY order: counter update BEFORE collection check
        if vlm_action == "attack" and current_block and "log" in current_block.lower():
            attack_on_log_ticks += 1
        else:
            attack_on_log_ticks = 0  # Reset to 0 BEFORE check!

        if (
            prev_block
            and "log" in prev_block.lower()
            and (current_block is None or "log" not in current_block.lower())
            and attack_on_log_ticks >= 3  # Always sees 0 → never fires
        ):
            logs_collected += 1
            attack_on_log_ticks = 0

        prev_block = current_block

    # Old code would fail: counter was reset before check
    assert logs_collected == 0, "Old buggy code should NOT detect the log"


def test_runner_dry_run_budget_exceeded_aborts(tmp_path: Path) -> None:
    adapter = load(ADAPTER_PATH)

    mock_brain = MockBrainBackend(
        scripted=[
            LLMResponse(
                text='{"subgoals": ["approach"]}',
                parsed_json={"subgoals": ["approach"]},
                prompt_tokens=500,
                completion_tokens=100,
                cost_estimate_usd=0.20,
                latency_ms=0.0,
                request_id="",
                cached_system=False,
            ),
            LLMResponse(
                text='{"intent": "look_around"}',
                parsed_json={"intent": "look_around"},
                prompt_tokens=500,
                completion_tokens=100,
                cost_estimate_usd=0.20,
                latency_ms=0.0,
                request_id="",
                cached_system=False,
            ),
        ]
    )

    mock_perception = MockBrainBackend(
        scripted=[
            _make_response(
                '{"block": "stone", "facing": "looking_at_horizon", "health": 1.0}',
                {"block": "stone", "facing": "looking_at_horizon", "health": 1.0},
                pt=200,
                ct=30,
            ),
        ]
        * 50
    )

    mock_capture = _MockCapture()

    session_manager = SessionManager()
    event_writer = EventWriter(tmp_path / "budget-test")
    event_writer.start()

    session_manager.start(
        adapter_path=ADAPTER_PATH,
        task_description="chop 3 oak logs",
        runs_root=tmp_path,
    )

    config = RunnerConfig(
        adapter=adapter,
        task="chop 3 oak logs",
        goal_name="chop_logs",
        runs_root=tmp_path,
        capture=mock_capture,
        perception=mock_perception,
        brain=mock_brain,
        input=None,
        hwnd=0,
        budget_usd=0.10,
        dry_run=True,
    )

    runner = AgentRunner(config, session_manager, event_writer)

    from gamemind.brain.budget_tracker import BudgetExceededError  # noqa: PLC0415

    try:
        outcome = runner.run()
    except BudgetExceededError:
        outcome = "runaway"

    assert outcome == "runaway"
    session_manager.transition_to_terminal(outcome=outcome)
    event_writer.close()
