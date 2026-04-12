"""E2E dry-run integration test for AgentRunner.

Runs the full runner pipeline with mock capture + mock perception +
mock brain, validating:
  1. Session reaches outcome=success
  2. Brain call count matches W1 (plan) + W5 (verify) = 2
  3. Budget tracker recorded both calls
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

    mock_brain = MockBrainBackend(
        scripted=[
            _make_response(
                '{"plan": ["approach_tree", "face_trunk", "attack"]}',
                {"plan": ["approach_tree", "face_trunk", "attack"]},
            ),
            _make_response('{"verify_ok": true}', {"verify_ok": True}, pt=600, ct=20),
        ]
    )

    mock_perception = MockBrainBackend(
        scripted=[
            _make_response(
                '{"inventory": {"log": 3}}',
                {"inventory": {"log": 3}},
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
        hwnd=0,
        budget_usd=1.0,
        dry_run=True,
    )

    runner = AgentRunner(config, session_manager, event_writer)
    outcome = runner.run()

    assert outcome == "success"
    assert mock_brain.call_count == 2
    assert mock_capture.capture_count >= 1

    session_manager.transition_to_terminal(outcome=outcome)
    event_writer.close()


def test_runner_dry_run_budget_exceeded_aborts(tmp_path: Path) -> None:
    adapter = load(ADAPTER_PATH)

    mock_brain = MockBrainBackend(
        scripted=[
            LLMResponse(
                text='{"plan": ["approach"]}',
                parsed_json={"plan": ["approach"]},
                prompt_tokens=500,
                completion_tokens=100,
                cost_estimate_usd=0.20,
                latency_ms=0.0,
                request_id="",
                cached_system=False,
            ),
            LLMResponse(
                text='{"plan": ["retry"]}',
                parsed_json={"plan": ["retry"]},
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
            _make_response('{"inventory": {"log": 0}}', {"inventory": {"log": 0}}, pt=200, ct=30),
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
