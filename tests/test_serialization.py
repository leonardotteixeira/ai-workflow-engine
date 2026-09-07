"""Round-trip serialization tests: model -> JSON -> model must be lossless
for every entity in the domain."""

from __future__ import annotations

from tests.factories import (
    NOW,
    branching_workflow,
    make_approval,
    make_execution,
    make_node_execution,
)
from workflow_engine.domain import (
    Event,
    EventType,
    ExecutionContext,
    NodeResult,
    RetryPolicy,
)
from workflow_engine.domain.node_execution import ErrorCategory, NodeExecutionError


def _roundtrip(model):
    cls = type(model)
    return cls.model_validate_json(model.model_dump_json())


def test_workflow_definition_roundtrip() -> None:
    wf = branching_workflow()
    restored = _roundtrip(wf)
    assert restored == wf
    assert restored.checksum == wf.checksum


def test_execution_roundtrip() -> None:
    execution = make_execution()
    restored = _roundtrip(execution)
    assert restored == execution


def test_execution_context_roundtrip() -> None:
    ctx = ExecutionContext(trigger_input={"a": 1}, variables={"b": [1, 2, {"c": 3}]})
    restored = _roundtrip(ctx)
    assert restored == ctx


def test_node_execution_roundtrip_with_error() -> None:
    running = make_node_execution().start(now=NOW)
    node_execution = running.fail(
        NodeExecutionError(category=ErrorCategory.TRANSIENT, message="timeout", details={"n": 1}),
        now=NOW,
    )
    restored = _roundtrip(node_execution)
    assert restored == node_execution


def test_approval_request_roundtrip() -> None:
    approval = make_approval()
    restored = _roundtrip(approval)
    assert restored == approval


def test_event_roundtrip() -> None:
    event = Event(
        event_id="e1",
        execution_id="exec-1",
        sequence=1,
        event_type=EventType.CONDITION_EVALUATED,
        node_id="classify",
        payload={"result": True},
        created_at=NOW,
    )
    restored = _roundtrip(event)
    assert restored == event


def test_node_result_roundtrip() -> None:
    result = NodeResult(status="completed", output={"score": 91}, context_patch={"score": 91})
    restored = _roundtrip(result)
    assert restored == result


def test_retry_policy_roundtrip() -> None:
    policy = RetryPolicy(
        max_attempts=5, backoff="EXPONENTIAL", base_delay_seconds=1, max_delay_seconds=60
    )
    restored = _roundtrip(policy)
    assert restored == policy
