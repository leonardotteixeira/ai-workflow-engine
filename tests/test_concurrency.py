"""Fase 8 — Concurrency (§8.7, §8.9, §8.10).

The retry runtime itself lives in test_retry.py; this file covers the
optimistic-concurrency (CAS) guarantees explicitly: Execution version
conflicts, duplicate execution resume, and approval idempotency — completing
the checklist Fase 8's brief asks for, on top of what Fase 6/7 already
exercised indirectly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from workflow_engine.domain import (
    Execution,
    ExecutionContext,
    ExecutionState,
    InvalidApprovalError,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.engine import ExecutionEngine, default_registry
from workflow_engine.engine.errors import InvalidExecutionStateError
from workflow_engine.persistence import (
    ConcurrencyConflictError,
    ExecutionRepository,
    PersistentExecutionEngine,
    create_schema,
    create_sqlite_engine,
)
from workflow_engine.persistence.repositories import ApprovalRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


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


def _pending(workflow: WorkflowDefinition, execution_id: str = "exec-1") -> Execution:
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


@pytest.fixture()
def db():
    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    yield engine
    engine.dispose()


def test_execution_repository_cas_rejects_stale_version(db) -> None:
    """Fase 8 §8.7's exact scenario: worker A reads version N, worker B reads
    the same version N, A commits (bumping the row to N+k), B's write with
    `expected_version=N` must fail rather than silently overwrite A's work."""
    workflow = _linear_workflow()
    execution = _pending(workflow)
    with db.begin() as conn:
        ExecutionRepository(conn).insert(execution)

    # Worker A reads version 1, advances it (in-memory), commits.
    worker_a_view = execution.transition_to(ExecutionState.RUNNING, now=NOW)
    with db.begin() as conn:
        ExecutionRepository(conn).update_cas(worker_a_view, expected_version=1)

    # Worker B *also* read version 1 (before A committed) and now tries to
    # write its own change using that stale version.
    worker_b_view = execution.transition_to(ExecutionState.CANCELLED, now=NOW)
    with db.begin() as conn, pytest.raises(ConcurrencyConflictError):
        ExecutionRepository(conn).update_cas(worker_b_view, expected_version=1)

    # A's write stands; B's was fully rejected, not partially applied.
    with db.begin() as conn:
        final = ExecutionRepository(conn).get(execution.id)
    assert final.state == ExecutionState.RUNNING
    assert final.version == worker_a_view.version


def test_duplicate_resume_of_the_same_execution_only_one_advances(db) -> None:
    """Fase 8 §8.9: the same execution resumed/run twice — only one call may
    actually advance it. Here this is guaranteed even more strongly than by
    CAS alone: once worker A's `run()` completes the execution, worker B's
    `run()` on the same execution_id is rejected by the domain state machine
    itself (COMPLETED accepts no further transitions) before persistence is
    even touched.
    """
    workflow = _linear_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result_a = persistent.start(_pending(workflow))
    assert result_a.execution.state == ExecutionState.COMPLETED

    with pytest.raises(InvalidExecutionStateError):
        persistent.run(result_a.execution.id)


def test_approval_resolution_is_idempotent_per_approval_id(db) -> None:
    """Fase 8 §8.10: approve(key=X) twice must not create two approvals or
    silently apply twice — this is the same guarantee Fase 6/7 already test
    via approve-after-approve; restated here explicitly against the
    persisted path to close the Fase 8 checklist."""
    from workflow_engine.domain.condition import ConditionLeaf

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
        id="wf-approval-idem",
        version=1,
        name="approval-idem",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending(workflow))
    approval_id = result.approval_request.approval_id

    persistent.resume(result.execution.id, approval_id, decision="approved", resolved_by="a")
    with pytest.raises(InvalidApprovalError):
        persistent.resume(result.execution.id, approval_id, decision="approved", resolved_by="a")

    with db.begin() as conn:
        rows = conn.execute(
            text("SELECT COUNT(*) FROM approval_requests WHERE approval_id = :aid"),
            {"aid": approval_id},
        ).scalar()
        final_approval = ApprovalRepository(conn).get(approval_id)

    assert rows == 1  # never duplicated — one row, updated in place
    assert final_approval.status.value == "APPROVED"
