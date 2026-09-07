"""NodeExecution — DESIGN.md §2.2, §3.2, §6.1.

A NodeExecution is a single attempt at running a node. It is write-once with
respect to `attempt`: a retry never mutates a failed NodeExecution back to
PENDING, it creates a brand new NodeExecution with `attempt + 1`
(`NodeExecution.retry()` below builds exactly that new instance). This keeps
the full history of every attempt intact for auditing, and makes
`(execution_id, node_id, attempt)` a stable identity for a given try — with
`idempotency_key` as the value the Engine uses to detect "this exact input was
already completed, don't re-run the side effect" across attempts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from workflow_engine.domain.enums import ErrorCategory, NodeExecutionStatus
from workflow_engine.domain.state_machine import (
    NODE_EXECUTION_TRANSITIONS,
    assert_transition_allowed,
)


class NodeExecutionError(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: ErrorCategory
    message: str
    details: dict[str, Any] = {}


class NodeExecution(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    execution_id: str
    node_id: str
    attempt: int = 1
    status: NodeExecutionStatus = NodeExecutionStatus.PENDING
    input: dict[str, Any] = {}
    output: dict[str, Any] | None = None
    error: NodeExecutionError | None = None
    idempotency_key: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None

    def transition_to(self, target: NodeExecutionStatus) -> NodeExecution:
        """Validate and apply a status transition (DESIGN.md §3.2). Timestamps
        are set by the caller via the dedicated `start`/`complete`/`fail`
        helpers below, not here — this method only enforces the state machine.
        """
        assert_transition_allowed(
            "NodeExecution", self.status, target, NODE_EXECUTION_TRANSITIONS
        )
        return self.model_copy(update={"status": target})

    def start(self, *, now: datetime) -> NodeExecution:
        return self.transition_to(NodeExecutionStatus.RUNNING).model_copy(
            update={"started_at": now}
        )

    def complete(self, output: dict[str, Any], *, now: datetime) -> NodeExecution:
        return self.transition_to(NodeExecutionStatus.COMPLETED).model_copy(
            update={"output": output, "finished_at": now}
        )

    def fail(self, error: NodeExecutionError, *, now: datetime) -> NodeExecution:
        return self.transition_to(NodeExecutionStatus.FAILED).model_copy(
            update={"error": error, "finished_at": now}
        )

    def wait(self) -> NodeExecution:
        return self.transition_to(NodeExecutionStatus.WAITING)

    def skip(self) -> NodeExecution:
        return self.transition_to(NodeExecutionStatus.SKIPPED)

    def retry(self, *, new_id: str, idempotency_key: str) -> NodeExecution:
        """Build the *new* NodeExecution for the next attempt after a FAILED one.

        Does not mutate `self`. Raises InvalidStateTransitionError unless the
        current record is FAILED — retrying anything else is a caller bug.
        """
        assert_transition_allowed(
            "NodeExecution",
            self.status,
            NodeExecutionStatus.PENDING,
            NODE_EXECUTION_TRANSITIONS,
        )
        return NodeExecution(
            id=new_id,
            execution_id=self.execution_id,
            node_id=self.node_id,
            attempt=self.attempt + 1,
            status=NodeExecutionStatus.PENDING,
            input=self.input,
            idempotency_key=idempotency_key,
        )
