from __future__ import annotations

from datetime import timedelta

import pytest

from tests.factories import NOW, make_approval, make_execution
from workflow_engine.domain import (
    ApprovalStatus,
    ExecutionState,
    InvalidApprovalError,
    approve,
    reject,
)


def _waiting_pair():
    execution = make_execution(state=ExecutionState.WAITING)
    approval = make_approval(status=ApprovalStatus.PENDING)
    return approval, execution


def test_approve_moves_execution_back_to_running_and_approval_to_approved() -> None:
    approval, execution = _waiting_pair()
    new_approval, new_execution = approve(
        approval, execution, resolved_by="alice@example.com", now=NOW + timedelta(seconds=1)
    )
    assert new_approval.status == ApprovalStatus.APPROVED
    assert new_approval.resolved_by == "alice@example.com"
    assert new_approval.resolved_at == NOW + timedelta(seconds=1)
    assert new_execution.state == ExecutionState.RUNNING
    # originals untouched
    assert approval.status == ApprovalStatus.PENDING
    assert execution.state == ExecutionState.WAITING


def test_reject_moves_execution_back_to_running_not_failed() -> None:
    """DESIGN.md §9.2: reject is a business outcome, not a technical failure —
    the Engine decides which edge to follow based on the node's output, so the
    Execution must be RUNNING (able to proceed), never FAILED, right after a
    reject is applied."""
    approval, execution = _waiting_pair()
    new_approval, new_execution = reject(
        approval, execution, resolved_by="bob@example.com", now=NOW
    )
    assert new_approval.status == ApprovalStatus.REJECTED
    assert new_execution.state == ExecutionState.RUNNING


def test_duplicate_approve_rejected() -> None:
    approval, execution = _waiting_pair()
    approved, running_execution = approve(approval, execution, resolved_by="a", now=NOW)
    # second approve against the *original* pending approval and the now-running
    # execution must fail on both counts
    with pytest.raises(InvalidApprovalError):
        approve(approved, running_execution, resolved_by="a", now=NOW)


def test_approve_after_reject_rejected() -> None:
    approval, execution = _waiting_pair()
    rejected, running_execution = reject(approval, execution, resolved_by="a", now=NOW)
    with pytest.raises(InvalidApprovalError):
        approve(rejected, running_execution, resolved_by="a", now=NOW)


def test_reject_after_approve_rejected() -> None:
    approval, execution = _waiting_pair()
    approved, running_execution = approve(approval, execution, resolved_by="a", now=NOW)
    with pytest.raises(InvalidApprovalError):
        reject(approved, running_execution, resolved_by="a", now=NOW)


def test_approval_after_execution_cancelled_rejected() -> None:
    approval, execution = _waiting_pair()
    cancelled = execution.transition_to(ExecutionState.CANCELLED, now=NOW)
    with pytest.raises(InvalidApprovalError):
        approve(approval, cancelled, resolved_by="a", now=NOW)


def test_approval_after_execution_completed_rejected() -> None:
    """An approval can't be resolved once the execution has already finished —
    even if, hypothetically, the ApprovalRequest record itself was never
    updated (e.g. it was orphaned by a cancellation elsewhere)."""
    approval, execution = _waiting_pair()
    running = execution.transition_to(ExecutionState.RUNNING, now=NOW)
    completed = running.transition_to(ExecutionState.COMPLETED, now=NOW)
    with pytest.raises(InvalidApprovalError):
        approve(approval, completed, resolved_by="a", now=NOW)


def test_approve_rejected_when_approval_already_resolved_even_if_execution_still_waiting() -> None:
    """Models a stale-read race: the caller holds a WAITING snapshot of the
    Execution but the ApprovalRequest was already resolved (e.g. by a
    concurrent request that also updated the execution, and this caller is
    now replaying an outdated view). The approval-status precondition must
    catch this independently of the execution-state precondition."""
    execution = make_execution(state=ExecutionState.WAITING)
    already_approved = make_approval(status=ApprovalStatus.APPROVED)
    with pytest.raises(InvalidApprovalError):
        approve(already_approved, execution, resolved_by="a", now=NOW)


def test_approval_for_wrong_execution_rejected() -> None:
    approval, execution = _waiting_pair()
    other_execution = make_execution(state=ExecutionState.WAITING, execution_id="exec-other")
    with pytest.raises(InvalidApprovalError):
        approve(approval, other_execution, resolved_by="a", now=NOW)
