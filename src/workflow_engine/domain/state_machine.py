"""Formal state machines for Execution, NodeExecution and ApprovalRequest.

Each transition table is the single source of truth for what is allowed.
`Execution`, `NodeExecution` and `ApprovalRequest` never mutate their own
`state`/`status` field directly — they go through `assert_transition_allowed`
here, which raises `InvalidStateTransitionError` for anything not explicitly
whitelisted. No state is reachable from a terminal state.
"""

from __future__ import annotations

from typing import TypeVar

from workflow_engine.domain.enums import ApprovalStatus, ExecutionState, NodeExecutionStatus
from workflow_engine.domain.errors import InvalidStateTransitionError

StateT = TypeVar("StateT", ExecutionState, NodeExecutionStatus, ApprovalStatus)

# DESIGN.md §3.1
EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PENDING: frozenset({ExecutionState.RUNNING, ExecutionState.CANCELLED}),
    ExecutionState.RUNNING: frozenset(
        {
            ExecutionState.WAITING,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
            ExecutionState.CANCELLED,
        }
    ),
    ExecutionState.WAITING: frozenset(
        {ExecutionState.RUNNING, ExecutionState.CANCELLED, ExecutionState.FAILED}
    ),
    ExecutionState.COMPLETED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
}

# DESIGN.md §3.2. A retry never reopens a NodeExecution: FAILED -> PENDING here
# is checked against the *failed* record purely as an eligibility guard, but it
# is realized by constructing a brand new NodeExecution (attempt + 1) starting
# at PENDING — the failed record itself is never mutated (see
# NodeExecution.retry()).
NODE_EXECUTION_TRANSITIONS: dict[NodeExecutionStatus, frozenset[NodeExecutionStatus]] = {
    NodeExecutionStatus.PENDING: frozenset(
        {NodeExecutionStatus.RUNNING, NodeExecutionStatus.SKIPPED}
    ),
    NodeExecutionStatus.RUNNING: frozenset(
        {NodeExecutionStatus.COMPLETED, NodeExecutionStatus.FAILED, NodeExecutionStatus.WAITING}
    ),
    NodeExecutionStatus.WAITING: frozenset({NodeExecutionStatus.RUNNING}),
    NodeExecutionStatus.COMPLETED: frozenset(),
    NodeExecutionStatus.FAILED: frozenset({NodeExecutionStatus.PENDING}),
    NodeExecutionStatus.SKIPPED: frozenset(),
}

# DESIGN.md §9. An ApprovalRequest only ever leaves PENDING once, in either
# direction — this table is intentionally the enforcement point that backs the
# compare-and-swap described in DESIGN.md §3.3 / §10 (persistence layer applies
# the actual `WHERE status = 'PENDING'` conditional update; the domain defines
# the precondition it must enforce).
APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
}


def assert_transition_allowed(
    entity: str,
    current: StateT,
    target: StateT,
    table: dict[StateT, frozenset[StateT]],
) -> None:
    """Raise InvalidStateTransitionError unless `current -> target` is whitelisted.

    A self-transition (current == target) is always rejected: every transition
    in this engine is caused by exactly one domain event, so re-applying the
    same state is either a bug upstream or a duplicate/replayed operation that
    the caller must treat explicitly (e.g. approval idempotency), not silently
    accept here.
    """
    allowed = table.get(current, frozenset())
    if target not in allowed:
        raise InvalidStateTransitionError(entity, current.value, target.value)
