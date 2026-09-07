from __future__ import annotations

import pytest

from workflow_engine.domain import (
    BackoffStrategy,
    ErrorCategory,
    ExecutionPolicy,
    InvalidPolicyError,
    RetryPolicy,
    TimeoutAction,
    TimeoutPolicy,
)


def test_valid_retry_policy() -> None:
    policy = RetryPolicy(
        max_attempts=3,
        backoff=BackoffStrategy.EXPONENTIAL,
        base_delay_seconds=1.0,
        max_delay_seconds=30.0,
    )
    assert policy.retry_on == [ErrorCategory.TRANSIENT]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(
            max_attempts=0,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=1,
            max_delay_seconds=1,
        ),
        dict(
            max_attempts=-1,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=1,
            max_delay_seconds=1,
        ),
        dict(
            max_attempts=3,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=0,
            max_delay_seconds=1,
        ),
        dict(
            max_attempts=3,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=-1,
            max_delay_seconds=1,
        ),
        dict(
            max_attempts=3,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=10,
            max_delay_seconds=5,
        ),
        dict(
            max_attempts=3,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=1,
            max_delay_seconds=5,
            retry_on=[],
        ),
        dict(
            max_attempts=3,
            backoff=BackoffStrategy.FIXED,
            base_delay_seconds=1,
            max_delay_seconds=5,
            retry_on=[ErrorCategory.VALIDATION],
        ),
    ],
)
def test_invalid_retry_policy_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(InvalidPolicyError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_valid_timeout_policy() -> None:
    policy = TimeoutPolicy(duration_seconds=30, on_timeout=TimeoutAction.FAIL)
    assert policy.duration_seconds == 30


@pytest.mark.parametrize("duration", [0, -1, -100])
def test_invalid_timeout_policy_rejected(duration: int) -> None:
    with pytest.raises(InvalidPolicyError):
        TimeoutPolicy(duration_seconds=duration, on_timeout=TimeoutAction.FAIL)


def test_execution_policy_defaults() -> None:
    policy = ExecutionPolicy()
    assert policy.max_execution_duration_seconds is None


@pytest.mark.parametrize("duration", [0, -1])
def test_invalid_execution_policy_rejected(duration: int) -> None:
    with pytest.raises(InvalidPolicyError):
        ExecutionPolicy(max_execution_duration_seconds=duration)


def test_policies_are_frozen() -> None:
    from pydantic import ValidationError

    policy = TimeoutPolicy(duration_seconds=10, on_timeout=TimeoutAction.RETRY)
    with pytest.raises(ValidationError):
        policy.duration_seconds = 20  # type: ignore[misc]
