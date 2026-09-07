"""Repositories — DESIGN.md §10, Fase 7 §7.6.

Each repository takes a SQLAlchemy `Connection` (never an `Engine` — the
caller controls the transaction boundary, see `unit_of_work.py`) and
translates between rows and the frozen domain models. No SQL leaks into
`workflow_engine.domain` or `workflow_engine.engine`; conversely, no domain
type leaks into `schema.py` — this module is the only place that knows both.

`ExecutionRepository.update_cas` and `ApprovalRepository.update_cas` are
where the optimistic-concurrency contract described in DESIGN.md §3.3/§10
actually gets enforced: `UPDATE ... WHERE id = ? AND version = ?`, raising
`ConcurrencyConflictError` when it affects zero rows.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Connection, select

from workflow_engine.domain import (
    ApprovalRequest,
    ApprovalStatus,
    Event,
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeExecution,
    WorkflowDefinition,
)
from workflow_engine.domain.node_execution import NodeExecutionError
from workflow_engine.persistence.errors import ConcurrencyConflictError, NotFoundError
from workflow_engine.persistence.schema import (
    approval_requests,
    events,
    executions,
    idempotency_records,
    node_executions,
    workflow_definitions,
)


class WorkflowDefinitionRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def save(self, workflow: WorkflowDefinition) -> None:
        """Workflow definitions are immutable once created (DESIGN.md §2.1) —
        this is an insert-only table; saving the same (id, version) twice is
        a no-op if the content is identical and an integrity error otherwise
        (SQLite enforces the primary key), which is the correct behavior: a
        version must never be silently overwritten.
        """
        existing = self.get(workflow.id, workflow.version)
        if existing is not None:
            return
        self._conn.execute(
            workflow_definitions.insert().values(
                id=workflow.id,
                version=workflow.version,
                definition_json=workflow.model_dump_json(),
            )
        )

    def get(self, workflow_id: str, version: int) -> WorkflowDefinition | None:
        row = self._conn.execute(
            select(workflow_definitions.c.definition_json).where(
                workflow_definitions.c.id == workflow_id,
                workflow_definitions.c.version == version,
            )
        ).first()
        if row is None:
            return None
        return WorkflowDefinition.model_validate_json(row.definition_json)


def _execution_to_row(execution: Execution) -> dict[str, object]:
    return {
        "id": execution.id,
        "workflow_definition_id": execution.workflow_definition_id,
        "workflow_version": execution.workflow_version,
        "state": execution.state.value,
        "context_json": execution.context.model_dump_json(),
        "current_node_ids_json": json.dumps(execution.current_node_ids),
        "created_at": execution.created_at.isoformat(),
        "updated_at": execution.updated_at.isoformat(),
        "version": execution.version,
    }


def _row_to_execution(row: object) -> Execution:
    return Execution(
        id=row.id,  # type: ignore[attr-defined]
        workflow_definition_id=row.workflow_definition_id,  # type: ignore[attr-defined]
        workflow_version=row.workflow_version,  # type: ignore[attr-defined]
        state=ExecutionState(row.state),  # type: ignore[attr-defined]
        context=ExecutionContext.model_validate_json(row.context_json),  # type: ignore[attr-defined]
        current_node_ids=json.loads(row.current_node_ids_json),  # type: ignore[attr-defined]
        created_at=datetime.fromisoformat(row.created_at),  # type: ignore[attr-defined]
        updated_at=datetime.fromisoformat(row.updated_at),  # type: ignore[attr-defined]
        version=row.version,  # type: ignore[attr-defined]
    )


class ExecutionRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(self, execution: Execution) -> None:
        self._conn.execute(executions.insert().values(**_execution_to_row(execution)))

    def update_cas(self, execution: Execution, *, expected_version: int) -> None:
        """`execution.version` is already the *new* version (the domain's
        transition methods bump it before this is ever called) — the WHERE
        clause guards on `expected_version`, the version the caller read
        before making its change. Zero rows affected means someone else
        updated this row first.
        """
        result = self._conn.execute(
            executions.update()
            .where(executions.c.id == execution.id, executions.c.version == expected_version)
            .values(**_execution_to_row(execution))
        )
        if result.rowcount == 0:
            raise ConcurrencyConflictError(
                f"execution {execution.id!r}: expected version {expected_version}, "
                "but it was not found at that version (concurrent update?)"
            )

    def get(self, execution_id: str) -> Execution | None:
        row = self._conn.execute(
            select(executions).where(executions.c.id == execution_id)
        ).first()
        return None if row is None else _row_to_execution(row)

    def get_or_raise(self, execution_id: str) -> Execution:
        execution = self.get(execution_id)
        if execution is None:
            raise NotFoundError(f"no execution with id {execution_id!r}")
        return execution

    def list_by_states(self, states: list[ExecutionState]) -> list[Execution]:
        values = [s.value for s in states]
        rows = self._conn.execute(select(executions).where(executions.c.state.in_(values))).all()
        return [_row_to_execution(row) for row in rows]


class NodeExecutionRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(self, node_execution: NodeExecution) -> None:
        self._conn.execute(
            node_executions.insert().values(
                id=node_execution.id,
                execution_id=node_execution.execution_id,
                node_id=node_execution.node_id,
                attempt=node_execution.attempt,
                status=node_execution.status.value,
                input_json=json.dumps(node_execution.input),
                output_json=(
                    json.dumps(node_execution.output)
                    if node_execution.output is not None
                    else None
                ),
                error_json=(
                    node_execution.error.model_dump_json()
                    if node_execution.error is not None
                    else None
                ),
                idempotency_key=node_execution.idempotency_key,
                started_at=(
                    node_execution.started_at.isoformat() if node_execution.started_at else None
                ),
                finished_at=(
                    node_execution.finished_at.isoformat() if node_execution.finished_at else None
                ),
                next_retry_at=(
                    node_execution.next_retry_at.isoformat()
                    if node_execution.next_retry_at
                    else None
                ),
            )
        )

    def list_by_execution(self, execution_id: str) -> list[NodeExecution]:
        rows = self._conn.execute(
            select(node_executions)
            .where(node_executions.c.execution_id == execution_id)
            .order_by(node_executions.c.started_at)
        ).all()
        return [self._row_to_node_execution(row) for row in rows]

    def get_latest_for_execution(self, execution_id: str) -> NodeExecution | None:
        rows = self.list_by_execution(execution_id)
        return rows[-1] if rows else None

    def _row_to_node_execution(self, row: object) -> NodeExecution:
        from workflow_engine.domain.enums import NodeExecutionStatus

        return NodeExecution(
            id=row.id,  # type: ignore[attr-defined]
            execution_id=row.execution_id,  # type: ignore[attr-defined]
            node_id=row.node_id,  # type: ignore[attr-defined]
            attempt=row.attempt,  # type: ignore[attr-defined]
            status=NodeExecutionStatus(row.status),  # type: ignore[attr-defined]
            input=json.loads(row.input_json),  # type: ignore[attr-defined]
            output=json.loads(row.output_json) if row.output_json else None,  # type: ignore[attr-defined]
            error=(
                NodeExecutionError.model_validate_json(row.error_json)  # type: ignore[attr-defined]
                if row.error_json  # type: ignore[attr-defined]
                else None
            ),
            idempotency_key=row.idempotency_key,  # type: ignore[attr-defined]
            started_at=datetime.fromisoformat(row.started_at) if row.started_at else None,  # type: ignore[attr-defined]
            finished_at=datetime.fromisoformat(row.finished_at) if row.finished_at else None,  # type: ignore[attr-defined]
            next_retry_at=(
                datetime.fromisoformat(row.next_retry_at) if row.next_retry_at else None  # type: ignore[attr-defined]
            ),
        )


class ApprovalRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(self, approval: ApprovalRequest) -> None:
        self._conn.execute(
            approval_requests.insert().values(
                approval_id=approval.approval_id,
                execution_id=approval.execution_id,
                node_id=approval.node_id,
                node_execution_id=approval.node_execution_id,
                status=approval.status.value,
                requested_at=approval.requested_at.isoformat(),
                resolved_at=approval.resolved_at.isoformat() if approval.resolved_at else None,
                resolved_by=approval.resolved_by,
                decision_payload_json=(
                    json.dumps(approval.decision_payload)
                    if approval.decision_payload is not None
                    else None
                ),
                version=approval.version,
            )
        )

    def update_cas(self, approval: ApprovalRequest, *, expected_version: int) -> None:
        result = self._conn.execute(
            approval_requests.update()
            .where(
                approval_requests.c.approval_id == approval.approval_id,
                approval_requests.c.version == expected_version,
            )
            .values(
                status=approval.status.value,
                resolved_at=approval.resolved_at.isoformat() if approval.resolved_at else None,
                resolved_by=approval.resolved_by,
                decision_payload_json=(
                    json.dumps(approval.decision_payload)
                    if approval.decision_payload is not None
                    else None
                ),
                version=approval.version,
            )
        )
        if result.rowcount == 0:
            raise ConcurrencyConflictError(
                f"approval {approval.approval_id!r}: expected version {expected_version}, "
                "but it was not found at that version (concurrent resolution?)"
            )

    def get(self, approval_id: str) -> ApprovalRequest | None:
        row = self._conn.execute(
            select(approval_requests).where(approval_requests.c.approval_id == approval_id)
        ).first()
        if row is None:
            return None
        return ApprovalRequest(
            approval_id=row.approval_id,
            execution_id=row.execution_id,
            node_id=row.node_id,
            node_execution_id=row.node_execution_id,
            status=ApprovalStatus(row.status),
            requested_at=datetime.fromisoformat(row.requested_at),
            resolved_at=datetime.fromisoformat(row.resolved_at) if row.resolved_at else None,
            resolved_by=row.resolved_by,
            decision_payload=(
                json.loads(row.decision_payload_json) if row.decision_payload_json else None
            ),
            version=row.version,
        )


class EventRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(self, event: Event) -> None:
        self._conn.execute(
            events.insert().values(
                event_id=event.event_id,
                execution_id=event.execution_id,
                sequence=event.sequence,
                event_type=event.event_type.value,
                node_id=event.node_id,
                payload_json=json.dumps(event.payload),
                created_at=event.created_at.isoformat(),
            )
        )

    def list_by_execution(self, execution_id: str) -> list[Event]:
        from workflow_engine.domain import EventType

        rows = self._conn.execute(
            select(events)
            .where(events.c.execution_id == execution_id)
            .order_by(events.c.sequence)
        ).all()
        return [
            Event(
                event_id=row.event_id,
                execution_id=row.execution_id,
                sequence=row.sequence,
                event_type=EventType(row.event_type),
                node_id=row.node_id,
                payload=json.loads(row.payload_json),
                created_at=datetime.fromisoformat(row.created_at),
            )
            for row in rows
        ]

    def max_sequence(self, execution_id: str) -> int:
        from sqlalchemy import func

        result = self._conn.execute(
            select(func.max(events.c.sequence)).where(events.c.execution_id == execution_id)
        ).scalar()
        return result or 0


class IdempotencyRepository:
    """API-layer request deduplication — Fase 10 §10.4. See schema.py's
    `idempotency_records` docstring for how this differs from the node-level
    idempotency key in `engine/retry.py`.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, idempotency_key: str, endpoint: str) -> tuple[int, dict[str, object]] | None:
        row = self._conn.execute(
            select(idempotency_records.c.status_code, idempotency_records.c.response_json).where(
                idempotency_records.c.idempotency_key == idempotency_key,
                idempotency_records.c.endpoint == endpoint,
            )
        ).first()
        if row is None:
            return None
        return row.status_code, json.loads(row.response_json)

    def store(
        self,
        idempotency_key: str,
        endpoint: str,
        *,
        status_code: int,
        response: dict[str, object],
        now: datetime,
    ) -> None:
        self._conn.execute(
            idempotency_records.insert().values(
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                status_code=status_code,
                response_json=json.dumps(response),
                created_at=now.isoformat(),
            )
        )
