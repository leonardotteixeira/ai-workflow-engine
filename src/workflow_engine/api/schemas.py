"""HTTP request/response schemas — Fase 10 §10.2.

Deliberately separate from the domain models for the two resources that
matter most for API evolution (`WorkflowDefinition`, `Execution`) — the HTTP
shape can change (add pagination, rename a field for REST convention)
without touching the domain. Smaller value types with no independent
evolution story (`RetryPolicy`, `TimeoutPolicy`, the condition DSL, `Event`)
are reused directly: they're already just validated data, and re-declaring
them here would be pure duplication for no isolation benefit.

Every list field has an explicit `max_length` — Fase 10 §10.9's "oversized
requests" — so a single field can't smuggle an unbounded payload through
FastAPI's own JSON parsing before whole-body size is even checked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workflow_engine.domain import EventType, ExecutionState
from workflow_engine.domain.condition import ConditionExpr
from workflow_engine.domain.enums import NodeType
from workflow_engine.domain.policies import RetryPolicy, TimeoutPolicy

_ID_MAX_LENGTH = 200
_NAME_MAX_LENGTH = 200


class WorkflowNodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    type: NodeType
    config: dict[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicy | None = None
    timeout_policy: TimeoutPolicy | None = None


class WorkflowEdgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    source_node_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    target_node_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    condition: ConditionExpr | None = None


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=_NAME_MAX_LENGTH)
    nodes: list[WorkflowNodeIn] = Field(min_length=1, max_length=500)
    edges: list[WorkflowEdgeIn] = Field(default_factory=list, max_length=1000)
    start_node_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)


class WorkflowResponse(BaseModel):
    id: str
    version: int
    name: str
    checksum: str
    node_count: int
    edge_count: int
    start_node_id: str
    created_at: datetime


class ExecutionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    workflow_version: int = Field(default=1, ge=1)
    trigger_input: dict[str, Any] = Field(default_factory=dict)


class WaitingApprovalOut(BaseModel):
    approval_id: str
    node_id: str


class ExecutionResponse(BaseModel):
    id: str
    workflow_definition_id: str
    workflow_version: int
    state: ExecutionState
    current_node_ids: list[str]
    context_variables: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime
    waiting_approval: WaitingApprovalOut | None = None


class ApprovalActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1, max_length=_ID_MAX_LENGTH)
    resolved_by: str = Field(min_length=1, max_length=_NAME_MAX_LENGTH)
    decision_payload: dict[str, Any] | None = None


class EventResponse(BaseModel):
    event_id: str
    execution_id: str
    sequence: int
    event_type: EventType
    node_id: str | None
    payload: dict[str, Any]
    created_at: datetime


class NodeHistoryEntryOut(BaseModel):
    node_id: str
    event_type: EventType
    sequence: int
    attempt: int | None


class ReplayResponse(BaseModel):
    execution_id: str
    trigger_input: dict[str, Any]
    variables: dict[str, Any]
    node_history: list[NodeHistoryEntryOut]
    state: ExecutionState | None


class ErrorResponse(BaseModel):
    """Uniform error shape for every non-2xx response (Fase 10 §10.3) — never
    a raw stack trace, never an internal exception's `str()`."""

    detail: str


class HealthResponse(BaseModel):
    status: str = "ok"
