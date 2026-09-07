"""Fase 8 — Retry + Idempotency + Concurrency.

Retry happens synchronously inside the Engine's loop (see engine.py's module
docstring for why: V1 has no scheduler, so there is no real backoff wait —
`next_retry_at` is computed and recorded but never actually waited on). Tests
here use `FlakyTransformExecutor`, a tiny test double that fails N times
before succeeding, to exercise the retry path deterministically and without
any real sleep.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workflow_engine.domain import (
    BackoffStrategy,
    ErrorCategory,
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeExecutionStatus,
    NodeResult,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.node_execution import NodeExecutionError
from workflow_engine.engine import ExecutionEngine
from workflow_engine.engine.node_executor import NodeExecutorRegistry
from workflow_engine.engine.retry import compute_backoff_delay, derive_idempotency_key

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FlakyExecutor:
    """Fails with the given category the first `fail_times` calls, then
    succeeds. Call count is tracked so tests can assert exactly how many
    attempts the Engine made — no real I/O, no real delay.
    """

    def __init__(self, fail_times: int, category: ErrorCategory = ErrorCategory.TRANSIENT) -> None:
        self.fail_times = fail_times
        self.category = category
        self.calls = 0

    def execute(self, node, context) -> NodeResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return NodeResult(
                status="failed",
                error=NodeExecutionError(
                    category=self.category, message=f"attempt {self.calls} failed"
                ),
            )
        return NodeResult(status="completed", output={"attempt": self.calls})


def _workflow_with_retry_node(policy: RetryPolicy | None) -> WorkflowDefinition:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="flaky", type=NodeType.TOOL, retry_policy=policy),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="flaky"),
        WorkflowEdge(id="e2", source_node_id="flaky", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-retry",
        version=1,
        name="retry",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def _make_execution(workflow: WorkflowDefinition) -> Execution:
    return Execution(
        id="exec-1",
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


def _registry_with(flaky: FlakyExecutor) -> NodeExecutorRegistry:
    from workflow_engine.domain.enums import NodeType as NT
    from workflow_engine.engine.node_executor import EndNodeExecutor, StartNodeExecutor

    registry = NodeExecutorRegistry()
    registry.register(NT.START, StartNodeExecutor())
    registry.register(NT.END, EndNodeExecutor())
    registry.register(NT.TOOL, flaky)
    return registry


# ---------------------------------------------------------------------------
# Backoff computation (pure)
# ---------------------------------------------------------------------------


def test_fixed_backoff_is_constant() -> None:
    policy = RetryPolicy(
        max_attempts=5, backoff=BackoffStrategy.FIXED, base_delay_seconds=2, max_delay_seconds=100
    )
    assert compute_backoff_delay(policy, 1) == timedelta(seconds=2)
    assert compute_backoff_delay(policy, 4) == timedelta(seconds=2)


def test_exponential_backoff_doubles_and_caps() -> None:
    policy = RetryPolicy(
        max_attempts=6,
        backoff=BackoffStrategy.EXPONENTIAL,
        base_delay_seconds=1,
        max_delay_seconds=10,
    )
    assert compute_backoff_delay(policy, 1) == timedelta(seconds=1)
    assert compute_backoff_delay(policy, 2) == timedelta(seconds=2)
    assert compute_backoff_delay(policy, 3) == timedelta(seconds=4)
    assert compute_backoff_delay(policy, 5) == timedelta(seconds=10)  # capped, would be 16


def test_idempotency_key_stable_for_same_input_varies_by_node_and_execution() -> None:
    k1 = derive_idempotency_key("exec-1", "node-a", {"x": 1})
    k2 = derive_idempotency_key("exec-1", "node-a", {"x": 1})
    k3 = derive_idempotency_key("exec-1", "node-b", {"x": 1})
    k4 = derive_idempotency_key("exec-2", "node-a", {"x": 1})
    k5 = derive_idempotency_key("exec-1", "node-a", {"x": 2})
    assert k1 == k2
    assert len({k1, k3, k4, k5}) == 4


def test_idempotency_key_is_order_independent_for_dict_content() -> None:
    k1 = derive_idempotency_key("e", "n", {"a": 1, "b": 2})
    k2 = derive_idempotency_key("e", "n", {"b": 2, "a": 1})
    assert k1 == k2


# ---------------------------------------------------------------------------
# Retry runtime (Engine)
# ---------------------------------------------------------------------------


def test_transient_failure_is_retried_until_success() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        backoff=BackoffStrategy.FIXED,
        base_delay_seconds=1,
        max_delay_seconds=10,
        retry_on=[ErrorCategory.TRANSIENT],
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=2)  # fails attempts 1,2; succeeds on 3
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.COMPLETED
    assert flaky.calls == 3
    flaky_attempts = [ne for ne in result.node_executions if ne.node_id == "flaky"]
    assert [ne.attempt for ne in flaky_attempts] == [1, 2, 3]
    assert [ne.status for ne in flaky_attempts] == [
        NodeExecutionStatus.FAILED,
        NodeExecutionStatus.FAILED,
        NodeExecutionStatus.COMPLETED,
    ]
    # every attempt shares the same idempotency key (same logical input)
    assert len({ne.idempotency_key for ne in flaky_attempts}) == 1


def test_retry_history_preserves_every_attempt_without_overwriting() -> None:
    """Fase 8 §8.2: 'Não sobrescrever silenciosamente a tentativa anterior.'"""
    policy = RetryPolicy(
        max_attempts=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=1, max_delay_seconds=10
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=2)
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)
    result = engine.run(_make_execution(workflow))

    flaky_attempts = [ne for ne in result.node_executions if ne.node_id == "flaky"]
    ids = [ne.id for ne in flaky_attempts]
    assert len(ids) == len(set(ids)) == 3  # three distinct, all-preserved records


def test_exhausting_max_attempts_fails_the_execution() -> None:
    """Fase 8 §8.5: max_attempts exceeded -> FAILED, no infinite loop."""
    policy = RetryPolicy(
        max_attempts=2, backoff=BackoffStrategy.FIXED, base_delay_seconds=1, max_delay_seconds=10
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=10)  # never succeeds within max_attempts
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.FAILED
    assert flaky.calls == 2  # stopped exactly at max_attempts, no more
    flaky_attempts = [ne for ne in result.node_executions if ne.node_id == "flaky"]
    assert len(flaky_attempts) == 2
    assert all(ne.status == NodeExecutionStatus.FAILED for ne in flaky_attempts)


def test_non_retryable_error_category_fails_immediately_without_retry() -> None:
    """Fase 8 §8.3: only categories listed in retry_on are retried."""
    policy = RetryPolicy(
        max_attempts=5,
        backoff=BackoffStrategy.FIXED,
        base_delay_seconds=1,
        max_delay_seconds=10,
        retry_on=[ErrorCategory.TRANSIENT],
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=10, category=ErrorCategory.PERMANENT)
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.FAILED
    assert flaky.calls == 1  # no retry attempted at all


def test_node_without_retry_policy_fails_immediately() -> None:
    workflow = _workflow_with_retry_node(policy=None)
    flaky = FlakyExecutor(fail_times=1)
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.FAILED
    assert flaky.calls == 1


def test_failed_attempt_records_next_retry_at_using_backoff() -> None:
    policy = RetryPolicy(
        max_attempts=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=5, max_delay_seconds=100
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=1)
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    first_attempt = next(
        ne for ne in result.node_executions if ne.node_id == "flaky" and ne.attempt == 1
    )
    assert first_attempt.next_retry_at == NOW + timedelta(seconds=5)


def test_node_retrying_event_emitted_for_each_retry() -> None:
    from workflow_engine.domain import EventType

    policy = RetryPolicy(
        max_attempts=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=1, max_delay_seconds=10
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=2)
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    retrying_events = [e for e in result.events if e.event_type == EventType.NODE_RETRYING]
    assert len(retrying_events) == 2
    assert [e.payload["attempt"] for e in retrying_events] == [1, 2]


def test_retry_does_not_break_defensive_loop_protection() -> None:
    """A node retried many times must not be confused with the graph-cycle
    defensive limit (which counts distinct nodes visited, not attempts)."""
    policy = RetryPolicy(
        max_attempts=20, backoff=BackoffStrategy.FIXED, base_delay_seconds=0.01, max_delay_seconds=1
    )
    workflow = _workflow_with_retry_node(policy)
    flaky = FlakyExecutor(fail_times=15)
    engine = ExecutionEngine(workflow, _registry_with(flaky), clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))  # must not raise
    assert result.execution.state == ExecutionState.COMPLETED
    assert flaky.calls == 16
