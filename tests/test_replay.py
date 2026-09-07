"""Fase 9 §9.5/§9.6/§9.11/§9.12 — replay.

Replay is pure reconstruction from an event log, never a re-execution
(no LLM/tool/network/DB calls — verified here both behaviorally and, like the
domain's own condition-DSL security test, by AST inspection of the module).
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from datetime import UTC, datetime

import pytest

from workflow_engine.domain import (
    BackoffStrategy,
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.condition import ConditionLeaf
from workflow_engine.engine import ExecutionEngine, ReplayError, default_registry, replay
from workflow_engine.engine.replay import EventType, validate_event_sequence

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _pending(workflow: WorkflowDefinition, execution_id: str = "exec-1") -> Execution:
    return Execution(
        id=execution_id,
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(trigger_input={"doc": "x.pdf"}),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


def _linear_workflow() -> WorkflowDefinition:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="t", type=NodeType.TRANSFORM, config={"set": {"risk_score": 91}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="t"),
        WorkflowEdge(id="e2", source_node_id="t", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf",
        version=1,
        name="wf",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


# ---------------------------------------------------------------------------
# Successful reconstruction
# ---------------------------------------------------------------------------


def test_replay_reconstructs_variables_and_trigger_input_for_a_completed_execution() -> None:
    workflow = _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    replayed = replay(result.events)

    assert replayed.execution_id == result.execution.id
    assert replayed.trigger_input == {"doc": "x.pdf"}
    assert replayed.variables == {"risk_score": 91}
    assert replayed.state == ExecutionState.COMPLETED


def test_replay_matches_the_live_executions_final_context() -> None:
    """The core promise: replaying the log alone reproduces what the live
    Execution.context.variables actually ended up being."""
    workflow = _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    replayed = replay(result.events)
    assert replayed.variables == result.execution.context.variables


def test_replay_of_failed_execution_reports_failed_state() -> None:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="bad", type=NodeType.TRANSFORM, config={"set": "not-a-dict"}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="bad"),
        WorkflowEdge(id="e2", source_node_id="bad", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="wf-fail",
        version=1,
        name="fail",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    replayed = replay(result.events)
    assert replayed.state == ExecutionState.FAILED


def test_replay_of_waiting_execution_reports_waiting_state() -> None:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="approval"),
        WorkflowEdge(id="e2", source_node_id="approval", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="wf-wait",
        version=1,
        name="wait",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    replayed = replay(result.events)
    assert replayed.state == ExecutionState.WAITING


def test_replay_of_reject_reports_completed_when_workflow_completes_after_reject() -> None:
    approved = ConditionLeaf(field="approval.decision", operator="eq", value="approved")
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="end_ok", type=NodeType.END),
        WorkflowNode(id="end_no", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="approval"),
        WorkflowEdge(id="e2", source_node_id="approval", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="end_ok", condition=approved),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="end_no"),
    ]
    workflow = WorkflowDefinition(
        id="wf-reject",
        version=1,
        name="reject",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_pending(workflow))
    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="rejected", resolved_by="bob"
    )

    replayed = replay(resumed.events)
    assert replayed.state == ExecutionState.COMPLETED
    assert replayed.variables["approval"]["decision"] == "rejected"


def test_replay_with_retries_reconstructs_full_node_history() -> None:
    from workflow_engine.domain import ErrorCategory, NodeResult
    from workflow_engine.domain.node_execution import NodeExecutionError
    from workflow_engine.engine.node_executor import (
        EndNodeExecutor,
        NodeExecutorRegistry,
        StartNodeExecutor,
    )

    policy = RetryPolicy(
        max_attempts=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=1, max_delay_seconds=10
    )
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="flaky", type=NodeType.TOOL, retry_policy=policy),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="flaky"),
        WorkflowEdge(id="e2", source_node_id="flaky", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="wf-retry",
        version=1,
        name="retry",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )

    class OnceFlaky:
        def __init__(self):
            self.calls = 0

        def execute(self, node, context):
            self.calls += 1
            if self.calls == 1:
                return NodeResult(
                    status="failed",
                    error=NodeExecutionError(category=ErrorCategory.TRANSIENT, message="boom"),
                )
            return NodeResult(status="completed", output={})

    registry = NodeExecutorRegistry()
    registry.register(NodeType.START, StartNodeExecutor())
    registry.register(NodeType.END, EndNodeExecutor())
    registry.register(NodeType.TOOL, OnceFlaky())

    engine = ExecutionEngine(workflow, registry, clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    replayed = replay(result.events)
    flaky_history = [h for h in replayed.node_history if h.node_id == "flaky"]
    assert [h.event_type for h in flaky_history] == [
        EventType.NODE_STARTED,
        EventType.NODE_FAILED,
        EventType.NODE_STARTED,
        EventType.NODE_COMPLETED,
    ]


def test_replay_of_empty_log_is_not_an_error() -> None:
    replayed = replay([])
    assert replayed.state is None
    assert replayed.variables == {}


# ---------------------------------------------------------------------------
# Corrupted history (§9.12) — must fail loudly
# ---------------------------------------------------------------------------


def _completed_events(workflow: WorkflowDefinition | None = None):
    workflow = workflow or _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    return engine.run(_pending(workflow)).events


def test_sequence_gap_raises() -> None:
    events = _completed_events()
    corrupted = [events[0], events[1]] + events[3:]  # skip sequence 3
    with pytest.raises(ReplayError, match="gap"):
        validate_event_sequence(corrupted)


def test_duplicated_sequence_raises() -> None:
    events = _completed_events()
    corrupted = events[:2] + [events[1]] + events[2:]  # sequence 2 appears twice
    with pytest.raises(ReplayError):
        validate_event_sequence(corrupted)


def test_mixed_execution_ids_raises() -> None:
    events_a = _completed_events()
    events_b = [e.model_copy(update={"execution_id": "other-exec"}) for e in _completed_events()]
    with pytest.raises(ReplayError, match="multiple execution_ids"):
        validate_event_sequence(events_a[:1] + events_b[:1])


def test_malformed_execution_started_payload_raises() -> None:
    events = _completed_events()
    malformed = [events[0].model_copy(update={"payload": {}})] + events[1:]
    with pytest.raises(ReplayError, match="EXECUTION_STARTED"):
        replay(malformed)


def test_malformed_node_completed_payload_raises() -> None:
    events = _completed_events()
    node_completed_index = next(
        i for i, e in enumerate(events) if e.event_type == EventType.NODE_COMPLETED
    )
    malformed = list(events)
    malformed[node_completed_index] = events[node_completed_index].model_copy(
        update={"payload": {"output": {}}}  # missing context_patch
    )
    with pytest.raises(ReplayError, match="NODE_COMPLETED"):
        replay(malformed)


def test_node_completed_event_missing_node_id_raises() -> None:
    events = _completed_events()
    node_completed_index = next(
        i for i, e in enumerate(events) if e.event_type == EventType.NODE_COMPLETED
    )
    corrupted = list(events)
    corrupted[node_completed_index] = events[node_completed_index].model_copy(
        update={"node_id": None}
    )
    with pytest.raises(ReplayError, match="missing node_id"):
        replay(corrupted)


def test_node_failed_event_missing_node_id_raises() -> None:
    events = _completed_events()
    node_started_index = next(
        i for i, e in enumerate(events) if e.event_type == EventType.NODE_STARTED
    )
    corrupted = list(events)
    corrupted[node_started_index] = events[node_started_index].model_copy(
        update={"event_type": EventType.NODE_FAILED, "node_id": None}
    )
    with pytest.raises(ReplayError, match="missing node_id"):
        replay(corrupted)


def test_replay_never_succeeds_on_corrupted_input_with_a_plausible_looking_result() -> None:
    """The critical property from §9.12: never produce a state that *looks*
    valid from corrupted input — it must raise, not guess."""
    events = _completed_events()
    corrupted = events[:-1]  # truncated: missing the final EXECUTION_COMPLETED event
    # Truncation alone is not corruption (a mid-flight log is valid — the
    # execution just hasn't finished from this log's point of view)...
    replayed = replay(corrupted)
    assert replayed.state != ExecutionState.COMPLETED  # correctly NOT "completed"

    # ...but an actual gap/duplicate in what IS present must still raise.
    truly_corrupted = corrupted[:1] + corrupted[3:]
    with pytest.raises(ReplayError):
        replay(truly_corrupted)


# ---------------------------------------------------------------------------
# Security: replay never touches infrastructure
# ---------------------------------------------------------------------------


def test_replay_module_has_no_infrastructure_imports() -> None:
    forbidden = {
        "socket",
        "httpx",
        "requests",
        "sqlite3",
        "sqlalchemy",
        "subprocess",
        "openai",
        "anthropic",
    }
    source = pathlib.Path(
        importlib.import_module("workflow_engine.engine.replay").__file__  # type: ignore[arg-type]
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(m.split(".")[0] in forbidden for m in imported)


def test_replay_module_calls_no_engine_or_provider_or_tool_apis() -> None:
    """Replay must never *import* ExecutionEngine/LLMProvider/Tool/providers/
    tools modules — that would make it a rerun, not a reconstruction. Checked
    via AST imports specifically (not a raw substring search) since the
    module's own docstring legitimately mentions these names as things it
    does NOT do."""
    forbidden_modules = {
        "workflow_engine.engine.engine",
        "workflow_engine.engine.providers",
        "workflow_engine.engine.tools",
    }
    source = pathlib.Path(
        importlib.import_module("workflow_engine.engine.replay").__file__  # type: ignore[arg-type]
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    assert not any(m in forbidden_modules for m in imported_modules)
