"""Application service layer — Fase 10 §10.1.

HTTP -> Router -> **Application Service** (this module) -> Execution Engine
-> Domain/Persistence. Routers only parse/validate the request and format the
response; every actual decision (which repository to call, how to build an
`ExecutionEngine`, how to translate a domain object into a response schema)
lives here, so it's testable without spinning up FastAPI at all if that's
ever useful, and so no router accumulates business logic over time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, select

from workflow_engine.api.schemas import (
    ApprovalActionRequest,
    ExecutionCreateRequest,
    ExecutionResponse,
    NodeHistoryEntryOut,
    ReplayResponse,
    WaitingApprovalOut,
    WorkflowCreateRequest,
    WorkflowResponse,
)
from workflow_engine.domain import Execution, ExecutionContext, ExecutionState, WorkflowDefinition
from workflow_engine.domain import WorkflowEdge as WorkflowEdgeModel
from workflow_engine.domain import WorkflowNode as WorkflowNodeModel
from workflow_engine.engine.engine import ExecutionEngine
from workflow_engine.engine.node_executor import NodeExecutorRegistry
from workflow_engine.engine.observability import redact_payload
from workflow_engine.engine.replay import replay as replay_events
from workflow_engine.persistence.errors import NotFoundError
from workflow_engine.persistence.persistent_engine import PersistentExecutionEngine
from workflow_engine.persistence.repositories import (
    EventRepository,
    ExecutionRepository,
    WorkflowDefinitionRepository,
)
from workflow_engine.persistence.schema import approval_requests


def _workflow_to_response(workflow: WorkflowDefinition) -> WorkflowResponse:
    return WorkflowResponse(
        id=workflow.id,
        version=workflow.version,
        name=workflow.name,
        checksum=workflow.checksum,
        node_count=len(workflow.nodes),
        edge_count=len(workflow.edges),
        start_node_id=workflow.start_node_id,
        created_at=workflow.created_at,
    )


def create_workflow(conn: Connection, request: WorkflowCreateRequest) -> WorkflowResponse:
    """Validation of the graph itself (DAG-only, START/END rules, condition
    shape, ...) happens for free inside `WorkflowDefinition`'s constructor
    (Fase 1/2) — this function never re-implements any of it, it just
    translates the HTTP schema into that constructor's inputs and persists
    the result. A malformed graph raises one of the domain's own errors
    (`InvalidWorkflowError`, `DuplicateNodeError`, `InvalidEdgeError`),
    mapped to 422 by `api/errors.py`.
    """
    nodes = [
        WorkflowNodeModel(
            id=n.id, type=n.type, config=n.config, retry_policy=n.retry_policy,
            timeout_policy=n.timeout_policy,
        )
        for n in request.nodes
    ]
    edges = [
        WorkflowEdgeModel(
            id=e.id, source_node_id=e.source_node_id, target_node_id=e.target_node_id,
            condition=e.condition,
        )
        for e in request.edges
    ]
    workflow = WorkflowDefinition(
        id=request.id,
        version=request.version,
        name=request.name,
        nodes=nodes,
        edges=edges,
        start_node_id=request.start_node_id,
        created_at=datetime.now(UTC),
    )
    repo = WorkflowDefinitionRepository(conn)
    repo.save(workflow)
    # Re-read rather than trust the just-built object: `save()` is a no-op if
    # this (id, version) already existed (DESIGN.md §2.1, definitions are
    # immutable), so the response must reflect what's actually persisted —
    # e.g. its real `created_at` — not a throwaway timestamp from this call.
    persisted = repo.get(workflow.id, workflow.version)
    assert persisted is not None  # just inserted or already existed
    return _workflow_to_response(persisted)


def get_workflow(conn: Connection, workflow_id: str, version: int) -> WorkflowResponse:
    workflow = WorkflowDefinitionRepository(conn).get(workflow_id, version)
    if workflow is None:
        raise NotFoundError(f"no workflow {workflow_id!r} at version {version}")
    return _workflow_to_response(workflow)


def _execution_to_response(
    conn: Connection, execution: Execution
) -> ExecutionResponse:
    waiting_approval = None
    if execution.state == ExecutionState.WAITING and execution.current_node_ids:
        # Look up the PENDING approval for the frontier node — at most one
        # can exist per DESIGN.md §9 (a new HumanApprovalNode is never
        # reached while the previous one is still unresolved).
        row = conn.execute(
            select(approval_requests.c.approval_id, approval_requests.c.node_id).where(
                approval_requests.c.execution_id == execution.id,
                approval_requests.c.status == "PENDING",
            )
        ).first()
        if row is not None:
            waiting_approval = WaitingApprovalOut(approval_id=row.approval_id, node_id=row.node_id)

    return ExecutionResponse(
        id=execution.id,
        workflow_definition_id=execution.workflow_definition_id,
        workflow_version=execution.workflow_version,
        state=execution.state,
        current_node_ids=execution.current_node_ids,
        context_variables=execution.context.variables,
        version=execution.version,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
        waiting_approval=waiting_approval,
    )


def start_execution(
    db_engine: Engine, registry: NodeExecutorRegistry, request: ExecutionCreateRequest
) -> ExecutionResponse:
    with db_engine.begin() as conn:
        workflow = WorkflowDefinitionRepository(conn).get(
            request.workflow_id, request.workflow_version
        )
        if workflow is None:
            raise NotFoundError(
                f"no workflow {request.workflow_id!r} at version {request.workflow_version}"
            )

    execution = Execution(
        id=str(uuid.uuid4()),
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(trigger_input=request.trigger_input),
        current_node_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    execution_engine = ExecutionEngine(workflow, registry)
    persistent = PersistentExecutionEngine(db_engine, execution_engine)
    result = persistent.start(execution)

    with db_engine.begin() as conn:
        return _execution_to_response(conn, result.execution)


def get_execution(conn: Connection, execution_id: str) -> ExecutionResponse:
    execution = ExecutionRepository(conn).get(execution_id)
    if execution is None:
        raise NotFoundError(f"no execution with id {execution_id!r}")
    return _execution_to_response(conn, execution)


def _resolve_workflow_and_engine(
    db_engine: Engine, registry: NodeExecutorRegistry, execution_id: str
) -> ExecutionEngine:
    with db_engine.begin() as conn:
        execution = ExecutionRepository(conn).get_or_raise(execution_id)
        workflow = WorkflowDefinitionRepository(conn).get(
            execution.workflow_definition_id, execution.workflow_version
        )
        if workflow is None:
            raise NotFoundError(
                f"execution {execution_id!r} references a workflow definition that "
                "no longer exists — this indicates a data integrity problem, not a "
                "normal 404"
            )
    return ExecutionEngine(workflow, registry)


def resolve_approval(
    db_engine: Engine,
    registry: NodeExecutorRegistry,
    execution_id: str,
    decision: str,
    request: ApprovalActionRequest,
) -> ExecutionResponse:
    execution_engine = _resolve_workflow_and_engine(db_engine, registry, execution_id)
    persistent = PersistentExecutionEngine(db_engine, execution_engine)
    result = persistent.resume(
        execution_id,
        request.approval_id,
        decision=decision,  # type: ignore[arg-type]
        resolved_by=request.resolved_by,
        decision_payload=request.decision_payload,
    )
    with db_engine.begin() as conn:
        return _execution_to_response(conn, result.execution)


def list_events(conn: Connection, execution_id: str) -> list[dict[str, object]]:
    """Events are read back with their payloads passed through
    `redact_payload` (Fase 9's observability redaction) before ever leaving
    the server — the persisted event log is the source of truth for
    replay/audit, but an HTTP response is a wider blast radius, so the same
    "never leak secret-shaped fields" rule from logging is applied here too.
    """
    execution = ExecutionRepository(conn).get(execution_id)
    if execution is None:
        raise NotFoundError(f"no execution with id {execution_id!r}")
    events = EventRepository(conn).list_by_execution(execution_id)
    return [
        {
            "event_id": e.event_id,
            "execution_id": e.execution_id,
            "sequence": e.sequence,
            "event_type": e.event_type,
            "node_id": e.node_id,
            "payload": redact_payload(e.payload),
            "created_at": e.created_at,
        }
        for e in events
    ]


def replay_execution(conn: Connection, execution_id: str) -> ReplayResponse:
    execution = ExecutionRepository(conn).get(execution_id)
    if execution is None:
        raise NotFoundError(f"no execution with id {execution_id!r}")
    events = EventRepository(conn).list_by_execution(execution_id)
    replayed = replay_events(events)
    return ReplayResponse(
        execution_id=replayed.execution_id,
        trigger_input=replayed.trigger_input,
        variables=replayed.variables,
        node_history=[
            NodeHistoryEntryOut(
                node_id=h.node_id, event_type=h.event_type, sequence=h.sequence, attempt=h.attempt
            )
            for h in replayed.node_history
        ],
        state=replayed.state,
    )
