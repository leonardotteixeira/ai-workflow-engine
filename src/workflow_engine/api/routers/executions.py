"""Executions router — Fase 10 §10.1/§10.4.

`POST /executions`, `.../approve`, `.../reject` all accept an optional
`Idempotency-Key` header (Fase 10 §10.4): a repeated request with the same
key against the same endpoint replays the first response instead of running
the operation again. This is a best-effort convenience on top of the
Engine's own real guarantees (domain state-machine preconditions + CAS,
DESIGN.md §17) — the idempotency-record write is its own small transaction,
not atomic with the underlying operation's transaction, so a crash in the
narrow window between them could in principle still double-apply. That
window is far narrower than "no idempotency key at all", but it is not a
formal exactly-once guarantee — consistent with DESIGN.md's stated semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, Engine

from workflow_engine.api import services
from workflow_engine.api.deps import get_connection, get_db_engine, get_node_registry
from workflow_engine.api.schemas import (
    ApprovalActionRequest,
    ErrorResponse,
    ExecutionCreateRequest,
    ExecutionResponse,
    ReplayResponse,
)
from workflow_engine.engine.node_executor import NodeExecutorRegistry
from workflow_engine.persistence.repositories import IdempotencyRepository

router = APIRouter(prefix="/executions", tags=["executions"])


def _replay_or_none(
    conn: Connection, idempotency_key: str | None, endpoint: str
) -> JSONResponse | None:
    if not idempotency_key:
        return None
    cached = IdempotencyRepository(conn).get(idempotency_key, endpoint)
    if cached is None:
        return None
    status_code, body = cached
    return JSONResponse(status_code=status_code, content=body)


def _store_response(
    conn: Connection,
    idempotency_key: str | None,
    endpoint: str,
    status_code: int,
    body: dict[str, object],
) -> None:
    if not idempotency_key:
        return
    IdempotencyRepository(conn).store(
        idempotency_key, endpoint, status_code=status_code, response=body, now=datetime.now(UTC)
    )


@router.post(
    "",
    response_model=ExecutionResponse,
    status_code=201,
    summary="Start a new execution of a registered workflow",
    responses={404: {"model": ErrorResponse, "description": "Workflow not found"}},
)
async def create_execution(
    request: ExecutionCreateRequest,
    db_engine: Annotated[Engine, Depends(get_db_engine)],
    registry: Annotated[NodeExecutorRegistry, Depends(get_node_registry)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExecutionResponse | JSONResponse:
    with db_engine.begin() as conn:
        cached = _replay_or_none(conn, idempotency_key, "POST /executions")
    if cached is not None:
        return cached

    response = services.start_execution(db_engine, registry, request)

    with db_engine.begin() as conn:
        _store_response(conn, idempotency_key, "POST /executions", 201, jsonable_encoder(response))
    return response


@router.get(
    "/{execution_id}",
    response_model=ExecutionResponse,
    summary="Fetch the current state of an execution",
    responses={404: {"model": ErrorResponse, "description": "Execution not found"}},
)
async def get_execution(
    execution_id: str, conn: Annotated[Connection, Depends(get_connection)]
) -> ExecutionResponse:
    return services.get_execution(conn, execution_id)


async def _resolve(
    execution_id: str,
    request: ApprovalActionRequest,
    decision: str,
    db_engine: Engine,
    registry: NodeExecutorRegistry,
    idempotency_key: str | None,
    endpoint: str,
) -> ExecutionResponse | JSONResponse:
    with db_engine.begin() as conn:
        cached = _replay_or_none(conn, idempotency_key, endpoint)
    if cached is not None:
        return cached

    response = services.resolve_approval(db_engine, registry, execution_id, decision, request)

    with db_engine.begin() as conn:
        _store_response(conn, idempotency_key, endpoint, 200, jsonable_encoder(response))
    return response


@router.post(
    "/{execution_id}/approve",
    response_model=ExecutionResponse,
    summary="Approve a pending Human-in-the-Loop decision",
    responses={
        404: {"model": ErrorResponse, "description": "Execution or approval not found"},
        409: {
            "model": ErrorResponse,
            "description": "Approval already resolved, or execution not WAITING",
        },
    },
)
async def approve_execution(
    execution_id: str,
    request: ApprovalActionRequest,
    db_engine: Annotated[Engine, Depends(get_db_engine)],
    registry: Annotated[NodeExecutorRegistry, Depends(get_node_registry)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExecutionResponse | JSONResponse:
    return await _resolve(
        execution_id,
        request,
        "approved",
        db_engine,
        registry,
        idempotency_key,
        f"POST /executions/{execution_id}/approve",
    )


@router.post(
    "/{execution_id}/reject",
    response_model=ExecutionResponse,
    summary="Reject a pending Human-in-the-Loop decision",
    responses={
        404: {"model": ErrorResponse, "description": "Execution or approval not found"},
        409: {
            "model": ErrorResponse,
            "description": "Approval already resolved, or execution not WAITING",
        },
    },
)
async def reject_execution(
    execution_id: str,
    request: ApprovalActionRequest,
    db_engine: Annotated[Engine, Depends(get_db_engine)],
    registry: Annotated[NodeExecutorRegistry, Depends(get_node_registry)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExecutionResponse | JSONResponse:
    return await _resolve(
        execution_id,
        request,
        "rejected",
        db_engine,
        registry,
        idempotency_key,
        f"POST /executions/{execution_id}/reject",
    )


@router.get(
    "/{execution_id}/events",
    summary="Full event history for an execution",
    responses={404: {"model": ErrorResponse, "description": "Execution not found"}},
)
async def list_events(
    execution_id: str, conn: Annotated[Connection, Depends(get_connection)]
) -> list[dict[str, object]]:
    return services.list_events(conn, execution_id)


@router.get(
    "/{execution_id}/replay",
    response_model=ReplayResponse,
    summary="Reconstruct execution state from its event log (no side effects)",
    responses={404: {"model": ErrorResponse, "description": "Execution not found"}},
)
async def replay_execution(
    execution_id: str, conn: Annotated[Connection, Depends(get_connection)]
) -> ReplayResponse:
    """Pure reconstruction — never re-invokes an LLM/Tool, never calls the
    Engine (DESIGN.md §13). See `engine/replay.py`."""
    return services.replay_execution(conn, execution_id)
