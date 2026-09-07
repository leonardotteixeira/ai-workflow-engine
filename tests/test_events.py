"""Fase 9 §9.1/§9.2 — event catalog and sequencing.

Verifies the Engine actually emits the DESIGN.md §11.1 catalog at the right
lifecycle points, and that sequence numbers are monotonic, gapless, and
execution-scoped (independent runs don't share a sequence space).
"""

from __future__ import annotations

from datetime import UTC, datetime

from workflow_engine.domain import (
    BackoffStrategy,
    ErrorCategory,
    Event,
    EventType,
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
from workflow_engine.engine import ExecutionEngine, default_registry

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _assert_sequence_is_gapless_and_monotonic(events: list[Event]) -> None:
    sequences = [e.sequence for e in events]
    assert sequences == list(range(1, len(sequences) + 1))


def _pending(workflow: WorkflowDefinition, execution_id: str = "exec-1") -> Execution:
    return Execution(
        id=execution_id,
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(trigger_input={"doc": "report.pdf"}),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


def _linear_workflow() -> WorkflowDefinition:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="t", type=NodeType.TRANSFORM, config={"set": {"x": 1}}),
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


def test_linear_completion_emits_expected_event_sequence() -> None:
    workflow = _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    event_types = [e.event_type for e in result.events]
    assert event_types == [
        EventType.EXECUTION_STARTED,
        EventType.NODE_STARTED,  # start
        EventType.NODE_COMPLETED,
        EventType.NODE_STARTED,  # t
        EventType.NODE_COMPLETED,
        EventType.NODE_STARTED,  # end
        EventType.NODE_COMPLETED,
        EventType.EXECUTION_COMPLETED,
    ]
    _assert_sequence_is_gapless_and_monotonic(result.events)


def test_execution_started_payload_carries_trigger_input() -> None:
    workflow = _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    started = result.events[0]
    assert started.event_type == EventType.EXECUTION_STARTED
    assert started.payload["trigger_input"] == {"doc": "report.pdf"}


def test_node_completed_payload_carries_context_patch() -> None:
    workflow = _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    t_completed = next(
        e
        for e in result.events
        if e.event_type == EventType.NODE_COMPLETED and e.node_id == "t"
    )
    assert t_completed.payload["context_patch"] == {"x": 1}


def test_condition_evaluated_event_records_the_chosen_branch() -> None:
    high = ConditionLeaf(field="score", operator="gte", value=50)
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="classify", type=NodeType.TRANSFORM, config={"set": {"score": 90}}),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="high", type=NodeType.END),
        WorkflowNode(id="low", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="classify"),
        WorkflowEdge(id="e2", source_node_id="classify", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="high", condition=high),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="low"),
    ]
    workflow = WorkflowDefinition(
        id="wf-branch",
        version=1,
        name="branch",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))

    condition_events = [e for e in result.events if e.event_type == EventType.CONDITION_EVALUATED]
    assert len(condition_events) == 1
    assert condition_events[0].node_id == "route"
    assert condition_events[0].payload["result_node_id"] == "high"


def test_failure_emits_node_failed_then_execution_failed() -> None:
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

    event_types = [e.event_type for e in result.events]
    assert event_types[-2:] == [EventType.NODE_FAILED, EventType.EXECUTION_FAILED]
    _assert_sequence_is_gapless_and_monotonic(result.events)


def test_waiting_emits_approval_requested_and_approve_emits_expected_tail() -> None:
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
        id="wf-approval",
        version=1,
        name="approval",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_pending(workflow))

    assert waiting.events[-1].event_type == EventType.APPROVAL_REQUESTED
    _assert_sequence_is_gapless_and_monotonic(waiting.events)

    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="approved", resolved_by="a"
    )
    resumed_types = [e.event_type for e in resumed.events]
    assert resumed_types[0] == EventType.EXECUTION_RESUMED
    assert EventType.APPROVAL_APPROVED in resumed_types
    assert resumed_types[-1] == EventType.EXECUTION_COMPLETED
    # sequence in this second call restarts at 1 (in-memory ExecutionEngine
    # calls don't know about prior calls) — cross-call continuity is
    # PersistentExecutionEngine's job (see test_persistence.py's
    # "sequences span both calls" assertion for the persisted version).
    assert resumed.events[0].sequence == 1


def test_retry_emits_failed_then_retrying_then_started_again() -> None:
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

    from workflow_engine.domain import NodeResult
    from workflow_engine.domain.node_execution import NodeExecutionError
    from workflow_engine.engine.node_executor import (
        EndNodeExecutor,
        NodeExecutorRegistry,
        StartNodeExecutor,
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

    flaky_events = [e for e in result.events if e.node_id == "flaky"]
    assert [e.event_type for e in flaky_events] == [
        EventType.NODE_STARTED,
        EventType.NODE_FAILED,
        EventType.NODE_RETRYING,
        EventType.NODE_STARTED,
        EventType.NODE_COMPLETED,
    ]
