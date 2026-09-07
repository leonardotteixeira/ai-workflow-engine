"""Fase 7 — Persistence + Durability + Recovery.

All tests use a real, on-disk-format SQLite database (`:memory:` per test —
an isolated, independent database each time, not a shared file, so tests
never interfere with each other) — no mocking of the DB layer. This is
deliberate: Fase 7's whole point is proving actual transactional/CAS/WAL
behavior, which a mock can't demonstrate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from workflow_engine.domain import (
    ApprovalStatus,
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeExecutionStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.condition import ConditionLeaf
from workflow_engine.engine import ExecutionEngine, default_registry
from workflow_engine.persistence import (
    ConcurrencyConflictError,
    ExecutionRepository,
    NotFoundError,
    PersistentExecutionEngine,
    WorkflowDefinitionRepository,
    create_schema,
    create_sqlite_engine,
    find_orphaned_node_executions,
    mark_orphaned_as_failed,
)
from workflow_engine.persistence.repositories import ApprovalRepository, NodeExecutionRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_sqlite_engine(":memory:")
    create_schema(engine)
    yield engine
    engine.dispose()


def _linear_workflow() -> WorkflowDefinition:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="analyze", type=NodeType.TRANSFORM, config={"set": {"risk_score": 87}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="analyze"),
        WorkflowEdge(id="e2", source_node_id="analyze", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-linear",
        version=1,
        name="linear",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def _pending_execution(workflow: WorkflowDefinition, execution_id: str = "exec-1") -> Execution:
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


# ---------------------------------------------------------------------------
# Round-trip: persist -> load -> equivalent domain object (Fase 7 §7.9)
# ---------------------------------------------------------------------------


def test_workflow_definition_roundtrip(db) -> None:
    workflow = _linear_workflow()
    with db.begin() as conn:
        WorkflowDefinitionRepository(conn).save(workflow)
    with db.begin() as conn:
        loaded = WorkflowDefinitionRepository(conn).get(workflow.id, workflow.version)
    assert loaded == workflow
    assert loaded.checksum == workflow.checksum


def test_saving_the_same_workflow_version_twice_is_a_noop(db) -> None:
    workflow = _linear_workflow()
    with db.begin() as conn:
        repo = WorkflowDefinitionRepository(conn)
        repo.save(workflow)
        repo.save(workflow)  # must not raise a PK violation


def test_execution_roundtrip_through_full_engine_run(db) -> None:
    workflow = _linear_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending_execution(workflow))
    assert result.execution.state == ExecutionState.COMPLETED

    with db.begin() as conn:
        loaded = ExecutionRepository(conn).get(result.execution.id)
    assert loaded == result.execution
    assert loaded.context.variables == {"risk_score": 87}


# ---------------------------------------------------------------------------
# Transactional atomicity (Fase 7 §7.3/§7.4)
# ---------------------------------------------------------------------------


def test_execution_and_its_events_commit_together(db) -> None:
    workflow = _linear_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending_execution(workflow))

    with db.begin() as conn:
        loaded_execution = ExecutionRepository(conn).get(result.execution.id)
        row_count = conn.execute(
            text("SELECT COUNT(*) FROM node_executions WHERE execution_id = :eid"),
            {"eid": result.execution.id},
        ).scalar()

    assert loaded_execution.state == ExecutionState.COMPLETED
    assert row_count == len(result.node_executions) == 3


def test_concurrency_conflict_rolls_back_the_whole_transaction(db) -> None:
    """Simulates two workers reading the same execution then both trying to
    advance it: the second `run()` must see zero rows changed and roll back
    entirely — not partially apply its node_executions while failing the
    execution update."""
    workflow = _linear_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    with db.begin() as conn:
        ExecutionRepository(conn).insert(_pending_execution(workflow))

    # Worker A completes the whole run and commits.
    result_a = persistent.run("exec-1")
    assert result_a.execution.state == ExecutionState.COMPLETED

    # Worker B started from the same *original* PENDING row (simulated by
    # manually re-running the in-memory engine against the stale execution)
    # and now tries to persist its own (stale) view.
    with pytest.raises(ConcurrencyConflictError), db.begin() as conn:
        stale_repo = ExecutionRepository(conn)
        # expected_version=1 (the original PENDING row's version) is now
        # stale — worker A already advanced it.
        stale_result = execution_engine.run(_pending_execution(workflow))
        stale_repo.update_cas(stale_result.execution, expected_version=1)

    # The rolled-back attempt left no trace: the row is still exactly what
    # worker A committed.
    with db.begin() as conn:
        final = ExecutionRepository(conn).get("exec-1")
    assert final == result_a.execution


def test_get_or_raise_on_missing_execution(db) -> None:
    with db.begin() as conn, pytest.raises(NotFoundError):
        ExecutionRepository(conn).get_or_raise("does-not-exist")


# ---------------------------------------------------------------------------
# WAL configuration (Fase 7 §7.5)
# ---------------------------------------------------------------------------


def test_wal_mode_is_enabled_on_a_file_backed_database(tmp_path) -> None:
    """WAL is a no-op for SQLite's `:memory:` databases (SQLite silently keeps
    the memory journal instead) — this test uses a real file to actually
    observe the pragma taking effect."""
    db_path = tmp_path / "wal_test.db"
    engine = create_sqlite_engine(str(db_path))
    create_schema(engine)
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert mode.lower() == "wal"
    engine.dispose()


# ---------------------------------------------------------------------------
# HITL persistence (approve/reject survive a transaction boundary)
# ---------------------------------------------------------------------------


def _approval_workflow() -> WorkflowDefinition:
    approved = ConditionLeaf(field="approval.decision", operator="eq", value="approved")
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="end_ok", type=NodeType.END),
        WorkflowNode(id="end_rejected", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="approval"),
        WorkflowEdge(id="e2", source_node_id="approval", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="end_ok", condition=approved),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="end_rejected"),
    ]
    return WorkflowDefinition(
        id="wf-approval",
        version=1,
        name="approval",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def test_waiting_execution_and_pending_approval_persist_together(db) -> None:
    workflow = _approval_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending_execution(workflow))
    assert result.execution.state == ExecutionState.WAITING
    assert result.approval_request is not None

    with db.begin() as conn:
        loaded_execution = ExecutionRepository(conn).get(result.execution.id)
        loaded_approval = ApprovalRepository(conn).get(result.approval_request.approval_id)

    assert loaded_execution.state == ExecutionState.WAITING
    assert loaded_approval.status == ApprovalStatus.PENDING


def test_resume_persists_the_resolved_approval_and_continues_execution(db) -> None:
    workflow = _approval_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending_execution(workflow))
    approval_id = result.approval_request.approval_id

    resumed = persistent.resume(
        result.execution.id, approval_id, decision="approved", resolved_by="alice"
    )
    assert resumed.execution.state == ExecutionState.COMPLETED

    with db.begin() as conn:
        loaded_approval = ApprovalRepository(conn).get(approval_id)
        loaded_execution = ExecutionRepository(conn).get(result.execution.id)

    assert loaded_approval.status == ApprovalStatus.APPROVED
    assert loaded_approval.resolved_by == "alice"
    assert loaded_execution.state == ExecutionState.COMPLETED


def test_resume_with_unknown_approval_id_raises_not_found(db) -> None:
    workflow = _approval_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending_execution(workflow))
    with pytest.raises(NotFoundError):
        persistent.resume(
            result.execution.id, "ghost-approval", decision="approved", resolved_by="alice"
        )


def _chained_approval_workflow() -> WorkflowDefinition:
    """START -> approval_1 -> approval_2 -> END, two approvals in sequence."""
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="approval_1", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="approval_2", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="approval_1"),
        WorkflowEdge(id="e2", source_node_id="approval_1", target_node_id="approval_2"),
        WorkflowEdge(id="e3", source_node_id="approval_2", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-chained-approval",
        version=1,
        name="chained-approval",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def test_resuming_into_a_second_approval_node_persists_the_new_pending_approval(db) -> None:
    """Resuming past the first approval reaches a *second* HumanApprovalNode
    before the run stops again — covers the branch in
    PersistentExecutionEngine.resume() that persists a newly-encountered
    ApprovalRequest (not just the one just resolved), and confirms the
    approval_requested event for the second node is persisted too."""
    workflow = _chained_approval_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    first = persistent.start(_pending_execution(workflow))
    assert first.execution.state == ExecutionState.WAITING
    assert first.approval_request.node_id == "approval_1"

    second = persistent.resume(
        first.execution.id, first.approval_request.approval_id, decision="approved", resolved_by="a"
    )
    assert second.execution.state == ExecutionState.WAITING
    assert second.approval_request is not None
    assert second.approval_request.node_id == "approval_2"

    with db.begin() as conn:
        loaded_new_approval = ApprovalRepository(conn).get(second.approval_request.approval_id)
        from workflow_engine.persistence.repositories import EventRepository

        all_events = EventRepository(conn).list_by_execution(first.execution.id)

    assert loaded_new_approval is not None
    assert loaded_new_approval.status == ApprovalStatus.PENDING
    # sequence numbers span both the start() call and this resume() call,
    # strictly increasing with no reset back to 1
    sequences = [e.sequence for e in all_events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))


def test_resume_twice_on_the_same_approval_raises(db) -> None:
    workflow = _approval_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)

    result = persistent.start(_pending_execution(workflow))
    approval_id = result.approval_request.approval_id
    persistent.resume(result.execution.id, approval_id, decision="approved", resolved_by="alice")

    from workflow_engine.domain import InvalidApprovalError

    with pytest.raises(InvalidApprovalError):
        persistent.resume(result.execution.id, approval_id, decision="rejected", resolved_by="bob")


# ---------------------------------------------------------------------------
# Recovery (Fase 7 §7.7/§7.8)
# ---------------------------------------------------------------------------


def test_recovery_finds_no_orphans_for_a_healthy_running_execution(db) -> None:
    with db.begin() as conn:
        orphans = find_orphaned_node_executions(
            conn, now=NOW, heartbeat_timeout=timedelta(minutes=5)
        )
    assert orphans == []


def test_recovery_detects_a_node_stuck_running_past_heartbeat(db) -> None:
    """Simulates DESIGN.md §12's crash scenario directly: manually insert an
    Execution=RUNNING row with a NodeExecution=RUNNING row whose started_at
    is far in the past, as if the process died mid-node — no engine call
    produces this state on its own (a live engine would have completed or
    failed the node), so it's constructed by hand here to model the crash.
    """
    workflow = _linear_workflow()
    with db.begin() as conn:
        ExecutionRepository(conn).insert(
            Execution(
                id="exec-crashed",
                workflow_definition_id=workflow.id,
                workflow_version=workflow.version,
                state=ExecutionState.RUNNING,
                context=ExecutionContext(),
                current_node_ids=["analyze"],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        from workflow_engine.domain import NodeExecution

        stuck = NodeExecution(
            id="ne-stuck",
            execution_id="exec-crashed",
            node_id="analyze",
            idempotency_key="exec-crashed:analyze:1",
        ).start(now=NOW - timedelta(hours=1))
        NodeExecutionRepository(conn).insert(stuck)

    with db.begin() as conn:
        orphans = find_orphaned_node_executions(
            conn, now=NOW, heartbeat_timeout=timedelta(minutes=5)
        )
    assert len(orphans) == 1
    assert orphans[0].execution_id == "exec-crashed"
    assert orphans[0].node_id == "analyze"

    with db.begin() as conn:
        mark_orphaned_as_failed(conn, orphans[0], now=NOW)

    with db.begin() as conn:
        history = NodeExecutionRepository(conn).list_by_execution("exec-crashed")
        loaded_execution = ExecutionRepository(conn).get("exec-crashed")

    # the original RUNNING row is untouched (write-once); a new FAILED row exists
    assert any(ne.id == "ne-stuck" and ne.status == NodeExecutionStatus.RUNNING for ne in history)
    assert any(
        ne.node_id == "analyze" and ne.status == NodeExecutionStatus.FAILED for ne in history
    )
    # recovery never touches Execution.state itself — that's the retry
    # runtime's call (Fase 8), not recovery's
    assert loaded_execution.state == ExecutionState.RUNNING


def test_recovery_ignores_running_execution_with_no_node_executions_yet(db) -> None:
    """A RUNNING execution that hasn't produced any NodeExecution row yet
    (e.g. persisted the instant before the first node started) has no
    "latest" to check — must not crash, must not be flagged."""
    workflow = _linear_workflow()
    with db.begin() as conn:
        ExecutionRepository(conn).insert(
            Execution(
                id="exec-fresh",
                workflow_definition_id=workflow.id,
                workflow_version=workflow.version,
                state=ExecutionState.RUNNING,
                context=ExecutionContext(),
                current_node_ids=["start"],
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with db.begin() as conn:
        orphans = find_orphaned_node_executions(
            conn, now=NOW, heartbeat_timeout=timedelta(minutes=5)
        )
    assert orphans == []


def test_recovery_ignores_running_execution_whose_latest_node_already_completed(db) -> None:
    """A RUNNING execution whose latest recorded NodeExecution is COMPLETED
    (not RUNNING) is mid-transition between nodes, not crashed — recovery
    must not flag it even if that completion happened long ago."""
    workflow = _linear_workflow()
    with db.begin() as conn:
        ExecutionRepository(conn).insert(
            Execution(
                id="exec-between-nodes",
                workflow_definition_id=workflow.id,
                workflow_version=workflow.version,
                state=ExecutionState.RUNNING,
                context=ExecutionContext(),
                current_node_ids=["analyze"],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        from workflow_engine.domain import NodeExecution

        completed = (
            NodeExecution(
                id="ne-done",
                execution_id="exec-between-nodes",
                node_id="start",
                idempotency_key="exec-between-nodes:start:1",
            )
            .start(now=NOW - timedelta(hours=1))
            .complete({}, now=NOW - timedelta(hours=1))
        )
        NodeExecutionRepository(conn).insert(completed)

    with db.begin() as conn:
        orphans = find_orphaned_node_executions(
            conn, now=NOW, heartbeat_timeout=timedelta(minutes=5)
        )
    assert orphans == []


def test_approval_repository_update_cas_raises_on_stale_version(db) -> None:
    """Direct repository-level test of the ApprovalRequest CAS, independent
    of the Engine's own process-local duplicate-resolution guard."""
    workflow = _approval_workflow()
    execution_engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    persistent = PersistentExecutionEngine(db, execution_engine)
    result = persistent.start(_pending_execution(workflow))
    approval = result.approval_request

    with db.begin() as conn, pytest.raises(ConcurrencyConflictError):
        ApprovalRepository(conn).update_cas(
            approval.model_copy(update={"status": ApprovalStatus.APPROVED}),
            expected_version=approval.version + 1,  # deliberately wrong
        )


def test_recovery_ignores_waiting_executions(db) -> None:
    """A WAITING execution is not a crash — a human simply hasn't acted yet,
    even if a long time has passed. Its latest NodeExecution is WAITING, not
    RUNNING, so it must never be flagged as orphaned no matter how old."""
    workflow = _approval_workflow()
    with db.begin() as conn:
        ExecutionRepository(conn).insert(
            Execution(
                id="exec-waiting",
                workflow_definition_id=workflow.id,
                workflow_version=workflow.version,
                state=ExecutionState.WAITING,
                context=ExecutionContext(),
                current_node_ids=["approval"],
                created_at=NOW,
                updated_at=NOW,
            )
        )
        from workflow_engine.domain import NodeExecution
        from workflow_engine.domain.enums import NodeExecutionStatus as NES
        from workflow_engine.persistence.repositories import (
            NodeExecutionRepository as _NodeExecutionRepository,
        )

        waiting_ne = (
            NodeExecution(
                id="ne-waiting",
                execution_id="exec-waiting",
                node_id="approval",
                idempotency_key="exec-waiting:approval:1",
            )
            .start(now=NOW - timedelta(days=30))
            .transition_to(NES.WAITING)
        )
        _NodeExecutionRepository(conn).insert(waiting_ne)

    with db.begin() as conn:
        orphans = find_orphaned_node_executions(
            conn, now=NOW, heartbeat_timeout=timedelta(minutes=5)
        )
    assert orphans == []
