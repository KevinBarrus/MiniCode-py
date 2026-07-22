"""Integration coverage for the real agent loop and cybernetic work chain."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import minicode.cybernetic_supervisor as cybernetic_supervisor
from minicode.agent_loop import run_agent_turn
from minicode.agent_intelligence import ToolScheduler
from minicode.context_compactor import AutoCompactConfig, ContextCompactor
from minicode.context_cybernetics import ContextCyberneticsOrchestrator
from minicode.context_manager import ContextManager
from minicode.decoupling_controller import DecouplingController
from minicode.feedback_controller import FeedbackController
from minicode.mock_model import MockModelAdapter
from minicode.permissions import PermissionManager
from minicode.self_healing_engine import FaultType, SelfHealingEngine
from minicode.state_observer import MeasurementVector, StateObserver
from minicode.tools import create_default_tool_registry


def _permissions(workspace: Path) -> PermissionManager:
    return PermissionManager(
        str(workspace),
        prompt=lambda request: {"decision": "allow_once"},
    )


def _run_same_task(workspace: Path, *, enable_work_chain: bool) -> dict[str, object]:
    workspace.mkdir(parents=True, exist_ok=True)
    messages = [
        {"role": "system", "content": "You are a coding assistant. Use tools to help the user."},
        {"role": "user", "content": "/write result.txt::integration-ok"},
    ]
    result = run_agent_turn(
        model=MockModelAdapter(),
        tools=create_default_tool_registry(str(workspace), runtime=None),
        messages=messages,
        cwd=str(workspace),
        permissions=_permissions(workspace),
        context_manager=(
            ContextManager(model="claude-sonnet-4-20250514")
            if enable_work_chain
            else None
        ),
        enable_work_chain=enable_work_chain,
        max_steps=3,
    )
    tool_calls = [message for message in result if message.get("role") == "assistant_tool_call"]
    tool_results = [message for message in result if message.get("role") == "tool_result"]
    return {
        "completed": (workspace / "result.txt").read_text() == "integration-ok",
        "steps": len(tool_calls),
        "tool_errors": sum(bool(message.get("isError")) for message in tool_results),
        "messages": len(result),
    }


def test_same_agent_task_has_comparable_baseline_and_cybernetic_arms(tmp_path, monkeypatch):
    """Run the same Mock LLM task with and without the work chain."""
    monkeypatch.setattr(
        cybernetic_supervisor,
        "SUPERVISOR_STATE_PATH",
        tmp_path / "cybernetic_supervisor.json",
    )
    baseline = _run_same_task(tmp_path / "baseline", enable_work_chain=False)
    cybernetic = _run_same_task(tmp_path / "cybernetic", enable_work_chain=True)

    assert baseline["completed"]
    assert cybernetic["completed"]
    assert baseline["steps"] == cybernetic["steps"] == 1
    assert baseline["tool_errors"] == cybernetic["tool_errors"] == 0
    assert baseline["messages"] > 0
    assert cybernetic["messages"] > 0


def test_agent_applies_feedforward_setpoints(tmp_path, monkeypatch):
    """The Agent wiring applies intent-specific targets to the feedback PID."""
    monkeypatch.setattr(
        cybernetic_supervisor,
        "SUPERVISOR_STATE_PATH",
        tmp_path / "cybernetic_supervisor.json",
    )
    applied: list[tuple[float, float, float]] = []
    original = FeedbackController.set_setpoints

    def capture(self, stability, performance, efficiency):
        applied.append((stability, performance, efficiency))
        original(self, stability, performance, efficiency)

    monkeypatch.setattr(FeedbackController, "set_setpoints", capture)
    result = _run_same_task(tmp_path / "setpoints", enable_work_chain=True)

    assert result["completed"]
    assert applied
    assert applied[0] == (0.85, 0.75, 0.60)


def test_context_pressure_flows_through_real_dual_pid_chain(tmp_path):
    """Context pressure reaches the outer feedback controller."""
    compactor = ContextCompactor(
        context_window=1000,
        workspace=str(tmp_path),
        memory_manager=MagicMock(),
        estimate_fn=lambda message: len(message.get("content", "")),
        config=AutoCompactConfig(),
    )
    context_orchestrator = ContextCyberneticsOrchestrator(
        compactor,
        pid_setpoint=0.70,
        base_threshold=0.60,
        enabled=True,
    )
    messages = [{"role": "user", "content": "x" * 2500}]

    compacted, result, _ = context_orchestrator.run_cycle(
        messages,
        error_rate=0.0,
        avg_latency=0.25,
        turn_id=1,
    )
    state = context_orchestrator.to_system_state()
    signal = FeedbackController().observe(state)

    assert result is not None
    assert len(compacted) <= len(messages)
    assert state.context_usage > 0.0
    assert signal.reason or signal.force_compaction or signal.adjust_token_budget != 1.0


def test_fault_signals_reach_state_observer_and_self_healing():
    """Simulated error bursts produce observed state and healing actions."""
    observer = StateObserver()
    scheduler = ToolScheduler()
    healing = SelfHealingEngine(tool_scheduler=scheduler)

    observed = None
    for index in range(8):
        observed = observer.update(
            MeasurementVector(
                timestamp=float(index),
                response_time=5.0 + index,
                success_rate=0.2,
                error_count=index + 1,
                context_length=1000 + index * 100,
            )
        )

    actions = healing.detect_and_heal(
        {
            "cpu_usage": 0.95,
            "memory_usage": 0.92,
            "error_rate": 4.5,
            "oscillation_index": 0.75,
        }
    )

    assert observed is not None
    assert observed.confidence > 0.0
    assert actions
    assert any(action.fault_type == FaultType.RESOURCE_EXHAUSTION for action in actions)
    assert scheduler._force_max_workers == 1


def test_decoupling_matrix_adjusts_pid_gains():
    """Strong measured coupling is applied to the corresponding PID."""
    decoupling = DecouplingController()
    feedback = FeedbackController()
    original_kp = feedback._performance_pid.kp

    for value in range(1, 7):
        decoupling.record_measurement({
            "token_usage_to_latency": (float(value), float(value * 2)),
        })

    adjustments = decoupling.apply_to_pid(None, feedback)

    assert adjustments["token_usage_to_latency"] > 0.5
    assert feedback._performance_pid.kp < original_kp
    assert decoupling.apply_to_pid(None, feedback)["token_usage_to_latency"] > 0.5
    assert feedback._performance_pid.kp < original_kp
