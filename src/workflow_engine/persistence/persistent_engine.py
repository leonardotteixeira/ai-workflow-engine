"""PersistentExecutionEngine — Fase 7 §7.3/§7.4.

Wraps the in-memory `ExecutionEngine` (Fase 3/6) with a SQLite-backed
transaction boundary: every `start`/`run`/`resume` call is one SQL
transaction that either persists the *entire* outcome — the updated
`Execution` row, every `NodeExecution` produced, every `Event` produced, and
any `ApprovalRequest` created/resolved — or persists none of it (DESIGN.md
§10, Fase 7 §7.3/§7.4: "não aceitar Execution=COMPLETED mas banco ainda
refletir RUNNING"). `sqlalchemy.Connection.begin()` gives us that atomicity;
if anything raises inside the `with` block (including a
`ConcurrencyConflictError` from the CAS update), the transaction rolls back
and none of the in-memory computation's side effects on the database exist.

Event sequence numbers: the in-memory `ExecutionEngine` starts counting from
0 on every call (it has no idea a previous call already wrote events for this
execution). This module is what stitches those into one true monotonic
per-execution sequence — it reads `EventRepository.max_sequence()` before
calling the engine and renumbers the returned events before persisting them.
"""

from __future__ import annotations

from sqlalchemy import Connection, Engine

from workflow_engine.domain import Execution
from workflow_engine.engine.engine import Decision, EngineRunResult, ExecutionEngine
from workflow_engine.persistence.errors import NotFoundError
from workflow_engine.persistence.repositories import (
    ApprovalRepository,
    EventRepository,
    ExecutionRepository,
    NodeExecutionRepository,
)


class PersistentExecutionEngine:
    def __init__(self, db: Engine, execution_engine: ExecutionEngine) -> None:
        self._db = db
        self._execution_engine = execution_engine

    def start(self, execution: Execution) -> EngineRunResult:
        """Insert a brand-new (PENDING) execution and immediately run it, all
        within one transaction — a caller can never observe a persisted
        execution row that hasn't at least reached its first real state.
        """
        with self._db.begin() as conn:
            ExecutionRepository(conn).insert(execution)
            return self._run_and_persist(conn, execution)

    def run(self, execution_id: str) -> EngineRunResult:
        """Load a previously-persisted execution and continue running it."""
        with self._db.begin() as conn:
            execution = ExecutionRepository(conn).get_or_raise(execution_id)
            return self._run_and_persist(conn, execution)

    def resume(
        self,
        execution_id: str,
        approval_id: str,
        *,
        decision: Decision,
        resolved_by: str,
        decision_payload: dict[str, object] | None = None,
    ) -> EngineRunResult:
        with self._db.begin() as conn:
            execution_repo = ExecutionRepository(conn)
            approval_repo = ApprovalRepository(conn)
            event_repo = EventRepository(conn)
            node_execution_repo = NodeExecutionRepository(conn)

            execution = execution_repo.get_or_raise(execution_id)
            approval = approval_repo.get(approval_id)
            if approval is None:
                raise NotFoundError(f"no approval request with id {approval_id!r}")

            expected_execution_version = execution.version
            expected_approval_version = approval.version
            base_sequence = event_repo.max_sequence(execution_id)

            result = self._execution_engine.resume(
                execution,
                approval,
                decision=decision,
                resolved_by=resolved_by,
                decision_payload=decision_payload,
            )

            renumbered = _renumber_events(result, base_sequence)
            execution_repo.update_cas(
                renumbered.execution, expected_version=expected_execution_version
            )
            for persisted_event in renumbered.events:
                event_repo.insert(persisted_event)

            assert renumbered.resolved_approval is not None  # resume() always sets this
            approval_repo.update_cas(
                renumbered.resolved_approval, expected_version=expected_approval_version
            )
            if renumbered.approval_request is not None:
                approval_repo.insert(renumbered.approval_request)

            for node_execution in renumbered.node_executions:
                node_execution_repo.insert(node_execution)

            return renumbered

    def _run_and_persist(self, conn: Connection, execution: Execution) -> EngineRunResult:
        execution_repo = ExecutionRepository(conn)
        event_repo = EventRepository(conn)
        node_execution_repo = NodeExecutionRepository(conn)
        approval_repo = ApprovalRepository(conn)

        expected_version = execution.version
        base_sequence = event_repo.max_sequence(execution.id)

        result = self._execution_engine.run(execution)

        renumbered = _renumber_events(result, base_sequence)
        execution_repo.update_cas(renumbered.execution, expected_version=expected_version)
        for node_execution in renumbered.node_executions:
            node_execution_repo.insert(node_execution)
        for persisted_event in renumbered.events:
            event_repo.insert(persisted_event)
        if renumbered.approval_request is not None:
            approval_repo.insert(renumbered.approval_request)

        return renumbered


def _renumber_events(result: EngineRunResult, base_sequence: int) -> EngineRunResult:
    renumbered_events = [
        event.model_copy(update={"sequence": base_sequence + i})
        for i, event in enumerate(result.events, start=1)
    ]
    return EngineRunResult(
        execution=result.execution,
        node_executions=result.node_executions,
        events=renumbered_events,
        approval_request=result.approval_request,
        resolved_approval=result.resolved_approval,
    )
