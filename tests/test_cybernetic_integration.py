"""Integration coverage for the real agent loop and cybernetic work chain."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import minicode.cybernetic_supervisor as cybernetic_supervisor
from minicode.agent_loop import run_agent_turn
from minicode.agent_intelligence import ToolScheduler
from minicode.context_compactor import AutoCompactConfig, ContextCompactor
from minicode.context_compactor import CompactStrategy, CompactTrigger, CompactionResult
from minicode.context_cybernetics import ContextCyberneticsOrchestrator, ControlAction
from minicode.context_manager import ContextManager
from minicode.decoupling_controller import DecouplingController
from minicode.feedback_controller import FeedbackController
from minicode.mock_model import MockModelAdapter
from minicode.permissions import PermissionManager
from minicode.predictive_controller import PredictiveAction
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


def test_predictive_compaction_executes_and_syncs_messages():
    """High-urgency predictive compaction updates the active message list."""
    from minicode.cybernetic_orchestrator import CyberneticOrchestrator

    messages = [{"role": "user", "content": "before"}]
    compacted_messages = [{"role": "user", "content": "after"}]
    result = SimpleNamespace(effective=True, tokens_freed=12)
    predictive = MagicMock()
    predictive.generate_predictive_actions.return_value = [
        PredictiveAction(
            action_type="preventive",
            urgency=0.9,
            metric_name="context_usage",
            predicted_issue="context overflow",
            recommended_action="trigger_compaction",
            expected_benefit="free context space",
        )
    ]
    context_cybernetics = MagicMock(enabled=True)
    context_cybernetics.run_cycle.return_value = (compacted_messages, result, None)
    context_manager = SimpleNamespace(
        messages=messages,
        get_stats=lambda: SimpleNamespace(usage_percentage=90.0),
    )

    orchestrator = CyberneticOrchestrator()
    orchestrator._initialized = True
    orchestrator.predictive = predictive
    orchestrator.context_cybernetics = context_cybernetics
    orchestrator.step_start(
        context_manager=context_manager,
        step=3,
        tool_error_count=0,
        saw_tool_result=False,
        actual_response_time=0.2,
        messages=messages,
    )

    context_cybernetics.run_cycle.assert_called_once()
    assert messages == compacted_messages
    assert context_manager.messages is messages


def test_oscillation_fault_flows_to_outer_feedback(tmp_path):
    """Alternating compaction outcomes reach the outer feedback signal."""
    compactor = ContextCompactor(
        context_window=1000,
        workspace=str(tmp_path),
        memory_manager=MagicMock(),
        estimate_fn=lambda message: len(message.get("content", "")),
        config=AutoCompactConfig(),
    )
    context_orchestrator = ContextCyberneticsOrchestrator(
        compactor,
        enabled=True,
    )
    context_orchestrator.run_cycle(
        [{"role": "user", "content": "x" * 100}],
        turn_id=1,
    )
    action = ControlAction(compaction_intensity=0.5, strategy=CompactStrategy.FULL)
    result = CompactionResult(
        success=True,
        strategy=CompactStrategy.FULL,
        trigger=CompactTrigger.MICROCOMPACT_CACHED,
        messages=[],
        tokens_freed=100,
    )
    for before, after in zip(
        [0.60, 0.72, 0.58, 0.71, 0.57, 0.73],
        [0.72, 0.58, 0.71, 0.57, 0.73, 0.59],
    ):
        context_orchestrator.feedback.record(action, result, before, after)

    state = context_orchestrator.to_system_state()
    signal = FeedbackController().observe(state)

    assert state.oscillation_index == 0.4
    assert signal.oscillation_index > 0.0
