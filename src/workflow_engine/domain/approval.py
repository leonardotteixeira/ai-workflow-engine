"""ApprovalRequest — DESIGN.md §2.3, §9.

`approve()`/`reject()` are plain module functions (not a service/repository
layer — that comes in a later phase) operating on an `(ApprovalRequest,
Execution)` pair, because the precondition that matters is cross-entity: an
approval may only be resolved while its owning `Execution` is `WAITING`
(DESIGN.md §3.3). They are pure: given the same inputs they always produce the
same outputs, and they never mutate their arguments.

Rejecting is a business decision, not a technical failure (DESIGN.md §9.2):
both `approve()` and `reject()` move the Execution back to RUNNING, and it is
up to the Engine (reading the resulting HumanApprovalNode output) to decide
which outgoing edge to follow next — this module does not know about edges.

The actual compare-and-swap (`UPDATE ... WHERE status = 'PENDING'`) is a
persistence-layer concern (DESIGN.md §3.3, §10). The precondition checks here
are what that CAS is enforcing; the domain guarantees that calling approve()/
reject() twice, or on a non-PENDING/non-WAITING pair, raises
`InvalidApprovalError` rather than silently succeeding twice.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from workflow_engine.domain.enums import ApprovalStatus, ExecutionState
from workflow_engine.domain.errors import InvalidApprovalError
from workflow_engine.domain.execution import Execution
from workflow_engine.domain.state_machine import APPROVAL_TRANSITIONS, assert_transition_allowed


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_id: str
    execution_id: str
    node_id: str
    node_execution_id: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    decision_payload: dict[str, Any] | None = None
    version: int = 1


def _resolve(
    approval: ApprovalRequest,
    execution: Execution,
    *,
    target_status: ApprovalStatus,
    resolved_by: str,
    decision_payload: dict[str, Any] | None,
    now: datetime,
) -> tuple[ApprovalRequest, Execution]:
    if approval.execution_id != execution.id:
        raise InvalidApprovalError(
            f"approval {approval.approval_id!r} does not belong to execution {execution.id!r}"
        )
    if execution.state != ExecutionState.WAITING:
        raise InvalidApprovalError(
            f"cannot resolve approval {approval.approval_id!r}: execution "
            f"{execution.id!r} is {execution.state.value}, not WAITING"
        )
    if approval.status != ApprovalStatus.PENDING:
        raise InvalidApprovalError(
            f"approval {approval.approval_id!r} is already {approval.status.value}"
        )
    assert_transition_allowed(
        "ApprovalRequest", approval.status, target_status, APPROVAL_TRANSITIONS
    )

    new_approval = approval.model_copy(
        update={
            "status": target_status,
            "resolved_at": now,
            "resolved_by": resolved_by,
            "decision_payload": decision_payload,
            "version": approval.version + 1,
        }
    )
    new_execution = execution.transition_to(ExecutionState.RUNNING, now=now)
    return new_approval, new_execution


def approve(
    approval: ApprovalRequest,
    execution: Execution,
    *,
    resolved_by: str,
    decision_payload: dict[str, Any] | None = None,
    now: datetime,
) -> tuple[ApprovalRequest, Execution]:
    return _resolve(
        approval,
        execution,
        target_status=ApprovalStatus.APPROVED,
        resolved_by=resolved_by,
        decision_payload=decision_payload,
        now=now,
    )


def reject(
    approval: ApprovalRequest,
    execution: Execution,
    *,
    resolved_by: str,
    decision_payload: dict[str, Any] | None = None,
    now: datetime,
) -> tuple[ApprovalRequest, Execution]:
    return _resolve(
        approval,
        execution,
        target_status=ApprovalStatus.REJECTED,
        resolved_by=resolved_by,
        decision_payload=decision_payload,
        now=now,
    )
