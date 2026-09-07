from __future__ import annotations

import pytest
from pydantic import ValidationError

from workflow_engine.domain import EventType, NodeResult
from workflow_engine.domain.event import EventDraft
from workflow_engine.domain.node_execution import ErrorCategory, NodeExecutionError


def test_completed_result_without_error() -> None:
    result = NodeResult(status="completed", output={"x": 1}, context_patch={"x": 1})
    assert result.error is None


def test_failed_result_requires_error() -> None:
    with pytest.raises(ValidationError):
        NodeResult(status="failed")


def test_completed_result_cannot_carry_error() -> None:
    with pytest.raises(ValidationError):
        NodeResult(
            status="completed",
            error=NodeExecutionError(category=ErrorCategory.TRANSIENT, message="x"),
        )


def test_failed_result_with_error() -> None:
    error = NodeExecutionError(category=ErrorCategory.PERMANENT, message="invalid input")
    result = NodeResult(status="failed", error=error)
    assert result.error == error
    assert result.output is None


def test_waiting_result_for_human_approval_node() -> None:
    result = NodeResult(
        status="waiting",
        events=[EventDraft(event_type=EventType.APPROVAL_REQUESTED, node_id="approval")],
    )
    assert result.events[0].event_type == EventType.APPROVAL_REQUESTED


def test_node_result_is_frozen() -> None:
    result = NodeResult(status="completed")
    with pytest.raises(ValidationError):
        result.status = "failed"  # type: ignore[misc]
