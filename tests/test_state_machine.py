from __future__ import annotations

from datetime import timedelta

import pytest

from tests.factories import NOW, make_execution, make_node_execution
from workflow_engine.domain import ExecutionState, InvalidStateTransitionError, NodeExecutionStatus
from workflow_engine.domain.node_execution import ErrorCategory, NodeExecutionError

# ---------------------------------------------------------------------------
# Execution state machine
# ---------------------------------------------------------------------------

VALID_EXECUTION_TRANSITIONS = [
    (ExecutionState.PENDING, ExecutionState.RUNNING),
    (ExecutionState.PENDING, ExecutionState.CANCELLED),
    (ExecutionState.RUNNING, ExecutionState.WAITING),
    (ExecutionState.RUNNING, ExecutionState.COMPLETED),
    (ExecutionState.RUNNING, ExecutionState.FAILED),
    (ExecutionState.RUNNING, ExecutionState.CANCELLED),
    (ExecutionState.WAITING, ExecutionState.RUNNING),
    (ExecutionState.WAITING, ExecutionState.CANCELLED),
    (ExecutionState.WAITING, ExecutionState.FAILED),
]


@pytest.mark.parametrize("current,target", VALID_EXECUTION_TRANSITIONS)
def test_valid_execution_transition(current: ExecutionState, target: ExecutionState) -> None:
    execution = make_execution(state=current)
    result = execution.transition_to(target, now=NOW + timedelta(seconds=1))
    assert result.state == target
    assert result.version == execution.version + 1
    assert result.updated_at == NOW + timedelta(seconds=1)
    # original is untouched (immutability)
    assert execution.state == current


INVALID_EXECUTION_TRANSITIONS = [
    (ExecutionState.COMPLETED, ExecutionState.RUNNING),
    (ExecutionState.COMPLETED, ExecutionState.WAITING),
    (ExecutionState.COMPLETED, ExecutionState.FAILED),
    (ExecutionState.COMPLETED, ExecutionState.CANCELLED),
    (ExecutionState.FAILED, ExecutionState.RUNNING),
    (ExecutionState.FAILED, ExecutionState.COMPLETED),
    (ExecutionState.CANCELLED, ExecutionState.RUNNING),
    (ExecutionState.CANCELLED, ExecutionState.COMPLETED),
    (ExecutionState.PENDING, ExecutionState.WAITING),
    (ExecutionState.PENDING, ExecutionState.COMPLETED),
    (ExecutionState.PENDING, ExecutionState.FAILED),
    (ExecutionState.WAITING, ExecutionState.COMPLETED),
    (ExecutionState.RUNNING, ExecutionState.PENDING),
    (ExecutionState.PENDING, ExecutionState.PENDING),
    (ExecutionState.RUNNING, ExecutionState.RUNNING),
    (ExecutionState.COMPLETED, ExecutionState.COMPLETED),
]


@pytest.mark.parametrize("current,target", INVALID_EXECUTION_TRANSITIONS)
def test_invalid_execution_transition_rejected(
    current: ExecutionState, target: ExecutionState
) -> None:
    execution = make_execution(state=current)
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        execution.transition_to(target, now=NOW)
    assert current.value in str(exc_info.value)
    assert target.value in str(exc_info.value)


def test_every_execution_state_is_covered_by_the_parametrized_matrix() -> None:
    """Guards against silently adding a new ExecutionState without extending
    the transition matrices above."""
    all_pairs = VALID_EXECUTION_TRANSITIONS + INVALID_EXECUTION_TRANSITIONS
    covered = {state for pair in all_pairs for state in pair}
    assert covered == set(ExecutionState)


# ---------------------------------------------------------------------------
# NodeExecution state machine
# ---------------------------------------------------------------------------

VALID_NODE_EXECUTION_TRANSITIONS = [
    (NodeExecutionStatus.PENDING, NodeExecutionStatus.RUNNING),
    (NodeExecutionStatus.PENDING, NodeExecutionStatus.SKIPPED),
    (NodeExecutionStatus.RUNNING, NodeExecutionStatus.COMPLETED),
    (NodeExecutionStatus.RUNNING, NodeExecutionStatus.FAILED),
    (NodeExecutionStatus.RUNNING, NodeExecutionStatus.WAITING),
    (NodeExecutionStatus.WAITING, NodeExecutionStatus.RUNNING),
]


@pytest.mark.parametrize("current,target", VALID_NODE_EXECUTION_TRANSITIONS)
def test_valid_node_execution_transition(
    current: NodeExecutionStatus, target: NodeExecutionStatus
) -> None:
    node_execution = make_node_execution(status=current)
    result = node_execution.transition_to(target)
    assert result.status == target


INVALID_NODE_EXECUTION_TRANSITIONS = [
    (NodeExecutionStatus.COMPLETED, NodeExecutionStatus.RUNNING),
    (NodeExecutionStatus.FAILED, NodeExecutionStatus.RUNNING),
    (NodeExecutionStatus.SKIPPED, NodeExecutionStatus.RUNNING),
    (NodeExecutionStatus.WAITING, NodeExecutionStatus.COMPLETED),
    (NodeExecutionStatus.PENDING, NodeExecutionStatus.COMPLETED),
    (NodeExecutionStatus.PENDING, NodeExecutionStatus.FAILED),
]


@pytest.mark.parametrize("current,target", INVALID_NODE_EXECUTION_TRANSITIONS)
def test_invalid_node_execution_transition_rejected(
    current: NodeExecutionStatus, target: NodeExecutionStatus
) -> None:
    node_execution = make_node_execution(status=current)
    with pytest.raises(InvalidStateTransitionError):
        node_execution.transition_to(target)


def test_retry_creates_new_node_execution_with_incremented_attempt() -> None:
    failed = make_node_execution(status=NodeExecutionStatus.FAILED)

    retried = failed.retry(new_id="ne-2", idempotency_key="key-2")

    assert retried.id == "ne-2"
    assert retried.attempt == failed.attempt + 1
    assert retried.status == NodeExecutionStatus.PENDING
    assert retried.idempotency_key == "key-2"
    # original failed record is untouched — history is preserved, not mutated
    assert failed.status == NodeExecutionStatus.FAILED
    assert failed.attempt == 1


def test_retry_rejected_when_not_failed() -> None:
    pending = make_node_execution(status=NodeExecutionStatus.PENDING)
    with pytest.raises(InvalidStateTransitionError):
        pending.retry(new_id="ne-2", idempotency_key="key-2")


def test_node_execution_start_complete_lifecycle_sets_timestamps() -> None:
    node_execution = make_node_execution(status=NodeExecutionStatus.PENDING)
    started = node_execution.start(now=NOW)
    assert started.started_at == NOW
    assert started.status == NodeExecutionStatus.RUNNING

    completed = started.complete({"result": 42}, now=NOW + timedelta(seconds=5))
    assert completed.output == {"result": 42}
    assert completed.finished_at == NOW + timedelta(seconds=5)
    assert completed.status == NodeExecutionStatus.COMPLETED


def test_node_execution_wait_transitions_to_waiting() -> None:
    node_execution = make_node_execution(status=NodeExecutionStatus.RUNNING)
    waited = node_execution.wait()
    assert waited.status == NodeExecutionStatus.WAITING


def test_node_execution_skip_transitions_to_skipped() -> None:
    node_execution = make_node_execution(status=NodeExecutionStatus.PENDING)
    skipped = node_execution.skip()
    assert skipped.status == NodeExecutionStatus.SKIPPED


def test_node_execution_fail_records_typed_error() -> None:
    node_execution = make_node_execution(status=NodeExecutionStatus.RUNNING)
    error = NodeExecutionError(category=ErrorCategory.PERMANENT, message="bad input")
    failed = node_execution.fail(error, now=NOW)
    assert failed.status == NodeExecutionStatus.FAILED
    assert failed.error == error
