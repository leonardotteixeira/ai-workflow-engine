from __future__ import annotations

from datetime import datetime

import pytest

from tests.engine_factories import (
    NOW,
    branching_risk_workflow,
    failing_workflow,
    linear_transform_workflow,
    llm_placeholder_workflow,
    make_pending_execution,
    three_step_workflow,
)
from workflow_engine.domain import ExecutionState, InvalidStateTransitionError, NodeExecutionStatus
from workflow_engine.engine import (
    DefensiveLoopLimitExceededError,
    ExecutionEngine,
    InvalidExecutionStateError,
    UnknownNodeExecutorError,
    default_registry,
)
from workflow_engine.engine.node_executor import NodeExecutorRegistry, TransformNodeExecutor


def _fixed_clock(t: datetime = NOW):
    return lambda: t


def _counting_id_factory():
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"id-{counter['n']}"

    return factory


# ---------------------------------------------------------------------------
# Linear execution
# ---------------------------------------------------------------------------


def test_linear_workflow_completes_and_applies_context_patch() -> None:
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(
        workflow, default_registry(), clock=_fixed_clock(), id_factory=_counting_id_factory()
    )
    execution = make_pending_execution(workflow)

    result = engine.run(execution)

    assert result.execution.state == ExecutionState.COMPLETED
    assert result.execution.context.variables == {"risk_score": 87}
    assert result.execution.current_node_ids == []
    assert [ne.node_id for ne in result.node_executions] == ["start", "analyze", "end"]
    assert all(ne.status == NodeExecutionStatus.COMPLETED for ne in result.node_executions)


def test_three_step_path_is_visited_in_order() -> None:
    workflow = three_step_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    result = engine.run(make_pending_execution(workflow))

    assert result.execution.state == ExecutionState.COMPLETED
    assert [ne.node_id for ne in result.node_executions] == ["start", "a", "b", "c", "end"]
    assert result.execution.context.variables == {"a": True, "b": True, "c": True}


# ---------------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------------


def test_high_risk_branch_reaches_human_approval_and_waits() -> None:
    workflow = branching_risk_workflow(risk_score=90)
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    result = engine.run(make_pending_execution(workflow))

    assert result.execution.state == ExecutionState.WAITING
    assert result.execution.current_node_ids == ["approval"]
    assert [ne.node_id for ne in result.node_executions] == [
        "start",
        "classify",
        "route",
        "approval",
    ]
    assert result.node_executions[-1].status == NodeExecutionStatus.WAITING


def test_low_risk_branch_skips_approval_and_completes() -> None:
    workflow = branching_risk_workflow(risk_score=10)
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    result = engine.run(make_pending_execution(workflow))

    assert result.execution.state == ExecutionState.COMPLETED
    assert [ne.node_id for ne in result.node_executions] == [
        "start",
        "classify",
        "route",
        "end_low",
    ]


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


def test_node_failure_moves_execution_to_failed_with_typed_error() -> None:
    workflow = failing_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    result = engine.run(make_pending_execution(workflow))

    assert result.execution.state == ExecutionState.FAILED
    failed_ne = result.node_executions[-1]
    assert failed_ne.node_id == "bad"
    assert failed_ne.status == NodeExecutionStatus.FAILED
    assert failed_ne.error is not None
    assert "must be a dict" in failed_ne.error.message
    # execution stops — "end" is never reached
    assert [ne.node_id for ne in result.node_executions] == ["start", "bad"]


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_before_running() -> None:
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    execution = make_pending_execution(workflow)
    cancelled = engine.cancel(execution)
    assert cancelled.state == ExecutionState.CANCELLED


def test_cancel_while_waiting() -> None:
    workflow = branching_risk_workflow(risk_score=90)
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    waiting = engine.run(make_pending_execution(workflow)).execution
    assert waiting.state == ExecutionState.WAITING

    cancelled = engine.cancel(waiting)
    assert cancelled.state == ExecutionState.CANCELLED


def test_cancel_after_completion_rejected() -> None:
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    completed = engine.run(make_pending_execution(workflow)).execution
    assert completed.state == ExecutionState.COMPLETED

    with pytest.raises(InvalidStateTransitionError):
        engine.cancel(completed)


def test_cancel_after_failure_rejected() -> None:
    workflow = failing_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    failed = engine.run(make_pending_execution(workflow)).execution
    assert failed.state == ExecutionState.FAILED

    with pytest.raises(InvalidStateTransitionError):
        engine.cancel(failed)


# ---------------------------------------------------------------------------
# Isolation between executions
# ---------------------------------------------------------------------------


def test_multiple_executions_do_not_contaminate_each_other() -> None:
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())

    exec_a = make_pending_execution(workflow, execution_id="exec-a")
    exec_b = make_pending_execution(workflow, execution_id="exec-b")

    result_a = engine.run(exec_a)
    result_b = engine.run(exec_b)

    assert result_a.execution.id == "exec-a"
    assert result_b.execution.id == "exec-b"
    assert result_a.execution.context.variables == result_b.execution.context.variables
    # the original inputs were never mutated by either run
    assert exec_a.state == ExecutionState.PENDING
    assert exec_b.state == ExecutionState.PENDING


def test_shared_registry_executor_instance_is_stateless_across_runs() -> None:
    """A single TransformNodeExecutor instance is reused by the registry across
    every node/execution — this test would fail if TransformNodeExecutor kept
    any mutable per-call state."""
    workflow = three_step_workflow()
    registry = NodeExecutorRegistry()
    shared_transform = TransformNodeExecutor()
    from workflow_engine.domain.enums import NodeType
    from workflow_engine.engine.node_executor import EndNodeExecutor, StartNodeExecutor

    registry.register(NodeType.START, StartNodeExecutor())
    registry.register(NodeType.END, EndNodeExecutor())
    registry.register(NodeType.TRANSFORM, shared_transform)

    engine = ExecutionEngine(workflow, registry, clock=_fixed_clock())
    r1 = engine.run(make_pending_execution(workflow, execution_id="e1"))
    r2 = engine.run(make_pending_execution(workflow, execution_id="e2"))

    assert r1.execution.context.variables == r2.execution.context.variables == {
        "a": True,
        "b": True,
        "c": True,
    }


# ---------------------------------------------------------------------------
# Unknown / missing executor
# ---------------------------------------------------------------------------


def test_unregistered_node_type_raises_unknown_executor_error() -> None:
    workflow = llm_placeholder_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    with pytest.raises(UnknownNodeExecutorError):
        engine.run(make_pending_execution(workflow))


def test_empty_registry_raises_on_first_node() -> None:
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(workflow, NodeExecutorRegistry(), clock=_fixed_clock())
    with pytest.raises(UnknownNodeExecutorError):
        engine.run(make_pending_execution(workflow))


# ---------------------------------------------------------------------------
# Invalid execution state for run()
# ---------------------------------------------------------------------------


def test_run_rejects_execution_for_a_different_workflow() -> None:
    workflow = linear_transform_workflow()
    other = three_step_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    execution = make_pending_execution(other)
    with pytest.raises(InvalidExecutionStateError):
        engine.run(execution)


def test_run_rejects_already_waiting_execution() -> None:
    workflow = branching_risk_workflow(risk_score=90)
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    waiting = engine.run(make_pending_execution(workflow)).execution
    with pytest.raises(InvalidExecutionStateError):
        engine.run(waiting)


def test_run_rejects_terminal_execution() -> None:
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    completed = engine.run(make_pending_execution(workflow)).execution
    with pytest.raises(InvalidExecutionStateError):
        engine.run(completed)


# ---------------------------------------------------------------------------
# Defensive loop protection
# ---------------------------------------------------------------------------


def test_defensive_loop_protection_on_bypassed_validation() -> None:
    """Build a 2-cycle graph via model_construct (bypassing WorkflowDefinition's
    validator entirely) and confirm the Engine fails loudly instead of hanging."""
    from workflow_engine.domain import WorkflowDefinition, WorkflowEdge, WorkflowNode
    from workflow_engine.domain.enums import NodeType

    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="a", type=NodeType.TRANSFORM),
        WorkflowNode(id="b", type=NodeType.TRANSFORM),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="a"),
        WorkflowEdge(id="e2", source_node_id="a", target_node_id="b"),
        WorkflowEdge(id="e3", source_node_id="b", target_node_id="a"),  # cycle, no END
    ]
    malformed = WorkflowDefinition.model_construct(
        id="wf-malformed",
        version=1,
        name="malformed",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )

    engine = ExecutionEngine(malformed, default_registry(), clock=_fixed_clock())
    execution = make_pending_execution(malformed)
    with pytest.raises(DefensiveLoopLimitExceededError):
        engine.run(execution)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_engine_works_with_default_clock_and_id_factory() -> None:
    """No clock/id_factory injected — exercises the real wall-clock/uuid
    defaults, just checking the run completes sanely (no assertions on the
    actual timestamp/id values, since those are non-deterministic by design)."""
    workflow = linear_transform_workflow()
    engine = ExecutionEngine(workflow, default_registry())
    result = engine.run(make_pending_execution(workflow))
    assert result.execution.state == ExecutionState.COMPLETED
    assert len(result.node_executions) == 3
    assert len({ne.id for ne in result.node_executions}) == 3  # ids are unique


def test_condition_node_with_no_matching_branch_and_no_default_raises() -> None:
    """Two conditioned edges (no default) where neither condition matches at
    runtime — structurally valid (>=2 edges) but unsatisfiable given this
    context. This is a graph-integrity problem the Engine surfaces loudly
    rather than silently failing the execution."""
    from workflow_engine.domain import WorkflowDefinition, WorkflowEdge, WorkflowNode
    from workflow_engine.domain.condition import ConditionLeaf
    from workflow_engine.domain.enums import NodeType
    from workflow_engine.engine import NoMatchingBranchError

    high = ConditionLeaf(field="risk_score", operator="gte", value=80)
    low = ConditionLeaf(field="risk_score", operator="lt", value=0)  # never true for risk_score=50
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="classify", type=NodeType.TRANSFORM, config={"set": {"risk_score": 50}}),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="end_high", type=NodeType.END),
        WorkflowNode(id="end_low", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="classify"),
        WorkflowEdge(id="e2", source_node_id="classify", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="end_high", condition=high),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="end_low", condition=low),
    ]
    workflow = WorkflowDefinition(
        id="wf-no-default",
        version=1,
        name="no-default",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
    with pytest.raises(NoMatchingBranchError):
        engine.run(make_pending_execution(workflow))


def test_same_workflow_and_input_produce_same_path_and_final_state() -> None:
    workflow = branching_risk_workflow(risk_score=90)

    def run_once(execution_id: str):
        engine = ExecutionEngine(workflow, default_registry(), clock=_fixed_clock())
        return engine.run(make_pending_execution(workflow, execution_id=execution_id))

    results = [run_once(f"exec-{i}") for i in range(5)]
    paths = [[ne.node_id for ne in r.node_executions] for r in results]
    states = [r.execution.state for r in results]
    contexts = [r.execution.context.variables for r in results]

    assert all(p == paths[0] for p in paths)
    assert all(s == states[0] for s in states)
    assert all(c == contexts[0] for c in contexts)
