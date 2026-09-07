"""NodeResult — DESIGN.md §6.1.

The envelope a Node returns from `execute(input, context) -> NodeResult`. A
Node never touches `ExecutionContext` directly — it returns `context_patch`,
and only the Engine (a later phase) decides how and when to merge it via
`ExecutionContext.with_patch`. Likewise a Node cannot append directly to the
execution's event log — it returns `events` as drafts; the Engine assigns
`event_id`/`sequence` when persisting them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from workflow_engine.domain.event import EventDraft
from workflow_engine.domain.node_execution import NodeExecutionError

NodeResultStatus = Literal["completed", "failed", "waiting"]


class NodeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: NodeResultStatus
    output: dict[str, Any] | None = None
    context_patch: dict[str, Any] = {}
    error: NodeExecutionError | None = None
    events: list[EventDraft] = []
    metadata: dict[str, Any] = {}

    @model_validator(mode="after")
    def _validate_error_consistency(self) -> NodeResult:
        if self.status == "failed" and self.error is None:
            raise ValueError("NodeResult with status='failed' must set `error`")
        if self.status != "failed" and self.error is not None:
            raise ValueError("NodeResult.error must only be set when status='failed'")
        return self
