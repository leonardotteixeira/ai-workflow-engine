"""Fase 6 — Human-in-the-Loop runtime.

Reuses the domain ApprovalRequest/approve()/reject() from Fase 1 verbatim
(no second state machine) — what's new here is `ExecutionEngine.resume()`
wiring those into the graph traversal, and the workflow-authoring pattern for
branching on the decision: a HUMAN_APPROVAL node stores `{node_id:
{"decision": "approved"|"rejected", ...}}` in the context, the same
convention every other node executor uses, so a CONDITION node placed right
after it can branch on `f"{node_id}.decision"` using the existing Fase 5
condition runtime — no special-cased branching logic for approval nodes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workflow_engine.domain import (
    ApprovalStatus,
    Execution,
    ExecutionContext,
    ExecutionState,
    InvalidApprovalError,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.condition import ConditionLeaf
from workflow_engine.engine import ExecutionEngine, default_registry

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _approval_then_branch_workflow() -> WorkflowDefinition:
    """START -> approval -> route(CONDITION) -> {approved: report, rejected: end}."""
    approved = ConditionLeaf(field="approval.decision", operator="eq", value="approved")
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="report", type=NodeType.TRANSFORM, config={"set": {"reported": True}}),
        WorkflowNode(id="end_reported", type=NodeType.END),
        WorkflowNode(id="end_rejected", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="approval"),
        WorkflowEdge(id="e2", source_node_id="approval", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="report", condition=approved),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="end_rejected"),
        WorkflowEdge(id="e5", source_node_id="report", target_node_id="end_reported"),
    ]
    return WorkflowDefinition(
        id="wf-hitl",
        version=1,
        name="hitl",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def _make_execution(workflow: WorkflowDefinition, execution_id: str = "exec-1") -> Execution:
    return Execution(
        id=execution_id,
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


def test_reaching_approval_node_waits_and_returns_a_pending_approval_request() -> None:
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.WAITING
    assert result.approval_request is not None
    assert result.approval_request.status == ApprovalStatus.PENDING
    assert result.approval_request.execution_id == result.execution.id
    assert result.approval_request.node_id == "approval"


def test_approve_resumes_and_follows_the_approved_branch() -> None:
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="approved", resolved_by="alice"
    )

    assert resumed.execution.state == ExecutionState.COMPLETED
    assert resumed.execution.context.variables["approval"] == {
        "decision": "approved",
        "resolved_by": "alice",
    }
    assert resumed.execution.context.variables["reported"] is True
    assert [ne.node_id for ne in resumed.node_executions] == [
        "approval",
        "route",
        "report",
        "end_reported",
    ]


def test_reject_is_a_business_outcome_not_a_technical_failure() -> None:
    """DESIGN.md §9.2 / Fase 6 §6.4: reject must NOT generically become FAILED
    — it follows whatever edge the workflow defines for the rejected branch,
    here reaching a normal END, and the execution COMPLETES."""
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="rejected", resolved_by="bob"
    )

    assert resumed.execution.state == ExecutionState.COMPLETED
    assert resumed.execution.context.variables["approval"]["decision"] == "rejected"
    assert [ne.node_id for ne in resumed.node_executions] == ["approval", "route", "end_rejected"]


# ---------------------------------------------------------------------------
# Duplicate / conflicting actions (Fase 6 §6.5, §6.6)
# ---------------------------------------------------------------------------


def test_approve_twice_with_the_original_pending_snapshot_is_rejected_second_time() -> None:
    """Using the *correct* pattern (feeding the returned execution back in)
    naturally fails on the second call because domain.approve() checks
    execution.state == WAITING, which is no longer true after the first."""
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="approved", resolved_by="alice"
    )
    assert resumed.execution.state == ExecutionState.COMPLETED

    with pytest.raises(InvalidApprovalError):
        engine.resume(
            resumed.execution, waiting.approval_request, decision="approved", resolved_by="alice"
        )


def test_concurrent_resolution_of_the_same_pending_snapshot_only_one_wins() -> None:
    """Fase 6 §6.6: two calls racing on the *same original* WAITING execution
    + PENDING approval snapshot (as would happen if two workers read the same
    row before either commits). Without a persisted CAS this can't be
    prevented by the domain objects alone (both look valid in isolation) — the
    Engine's process-local `_resolved_approval_ids` ledger is what actually
    rejects the second one here. See engine.py's constructor docstring for
    why this isn't the full cross-process guarantee (that's Fase 7)."""
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    # Worker A approves.
    result_a = engine.resume(
        waiting.execution, waiting.approval_request, decision="approved", resolved_by="worker-a"
    )
    assert result_a.execution.state == ExecutionState.COMPLETED

    # Worker B, holding the *same stale* WAITING/PENDING snapshot, tries to reject.
    with pytest.raises(InvalidApprovalError):
        engine.resume(
            waiting.execution, waiting.approval_request, decision="rejected", resolved_by="worker-b"
        )


def test_reject_after_approve_rejected_via_engine_resume() -> None:
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    engine.resume(waiting.execution, waiting.approval_request, decision="approved", resolved_by="a")
    with pytest.raises(InvalidApprovalError):
        engine.resume(
            waiting.execution, waiting.approval_request, decision="rejected", resolved_by="a"
        )


# ---------------------------------------------------------------------------
# Security / isolation (Fase 6 §6.7)
# ---------------------------------------------------------------------------


def test_approval_for_a_different_execution_is_rejected() -> None:
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting_a = engine.run(_make_execution(workflow, execution_id="exec-a"))
    waiting_b = engine.run(_make_execution(workflow, execution_id="exec-b"))
    assert waiting_a.approval_request is not None

    with pytest.raises(InvalidApprovalError):
        engine.resume(
            waiting_b.execution, waiting_a.approval_request, decision="approved", resolved_by="eve"
        )


def test_approval_with_fabricated_node_id_is_rejected() -> None:
    """Even with the correct execution_id, an ApprovalRequest whose node_id
    doesn't match what the execution is actually waiting on is not trusted —
    Fase 6 §6.7: "não confiar apenas em IDs fornecidos externamente"."""
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    forged = waiting.approval_request.model_copy(update={"node_id": "route"})
    with pytest.raises(InvalidApprovalError):
        engine.resume(waiting.execution, forged, decision="approved", resolved_by="eve")


def test_approval_after_cancellation_rejected() -> None:
    workflow = _approval_then_branch_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    cancelled = engine.cancel(waiting.execution)
    with pytest.raises(InvalidApprovalError):
        engine.resume(cancelled, waiting.approval_request, decision="approved", resolved_by="a")


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_approval_node_directly_followed_by_end_completes_on_resume() -> None:
    """A workflow that doesn't care about the decision at all: approval -> END
    directly. Since a non-END node always has exactly one outgoing edge
    (graph validation rule 6), resume() hands off to `_loop` which then runs
    the END node itself — there is no "approval has zero outgoing edges" case
    to reach (same invariant that makes workflow.py's own END-reachability
    fallback unreachable, see DESIGN.md §4.1)."""
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
        id="wf-direct-end",
        version=1,
        name="direct-end",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_make_execution(workflow))
    assert waiting.approval_request is not None

    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="approved", resolved_by="a"
    )
    assert resumed.execution.state == ExecutionState.COMPLETED
    assert resumed.execution.current_node_ids == []
    assert [ne.node_id for ne in resumed.node_executions] == ["approval", "end"]


def test_same_decision_produces_same_path_every_time() -> None:
    workflow = _approval_then_branch_workflow()

    def run_and_approve(execution_id: str):
        engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
        waiting = engine.run(_make_execution(workflow, execution_id=execution_id))
        assert waiting.approval_request is not None
        return engine.resume(
            waiting.execution, waiting.approval_request, decision="approved", resolved_by="a"
        )

    results = [run_and_approve(f"exec-{i}") for i in range(5)]
    paths = [[ne.node_id for ne in r.node_executions] for r in results]
    assert all(p == paths[0] for p in paths)
