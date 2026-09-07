from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from workflow_engine.api import services
from workflow_engine.api.deps import get_connection
from workflow_engine.api.schemas import ErrorResponse, WorkflowCreateRequest, WorkflowResponse

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=201,
    summary="Register a workflow definition",
    responses={422: {"model": ErrorResponse, "description": "Invalid workflow graph"}},
)
async def create_workflow(
    request: WorkflowCreateRequest, conn: Annotated[Connection, Depends(get_connection)]
) -> WorkflowResponse:
    """Validates the graph (DAG-only, exactly one START, no dangling edges,
    ...) using the exact same `WorkflowDefinition` constructor the Engine
    itself trusts — there is no separate, looser validation path for the API.
    Saving the same `(id, version)` twice is a no-op (DESIGN.md §2.1:
    definitions are immutable once created), not an error.
    """
    return services.create_workflow(conn, request)


@router.get(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Fetch a workflow definition",
    responses={404: {"model": ErrorResponse, "description": "Workflow not found"}},
)
async def get_workflow(
    workflow_id: str,
    conn: Annotated[Connection, Depends(get_connection)],
    version: Annotated[int, Query(ge=1)] = 1,
) -> WorkflowResponse:
    return services.get_workflow(conn, workflow_id, version)
