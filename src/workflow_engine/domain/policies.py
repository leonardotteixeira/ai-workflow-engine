"""RetryPolicy, TimeoutPolicy, ExecutionPolicy — DESIGN.md §2.4.

These are immutable configuration objects. They describe *what* should happen
(how many attempts, how long to wait) — they never execute anything.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from workflow_engine.domain.enums import (
    BackoffStrategy,
    ErrorCategory,
    FailurePropagation,
    TimeoutAction,
)
from workflow_engine.domain.errors import InvalidPolicyError


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_attempts: int
    backoff: BackoffStrategy
    base_delay_seconds: float
    max_delay_seconds: float
    retry_on: list[ErrorCategory] = [ErrorCategory.TRANSIENT]

    @model_validator(mode="after")
    def _validate(self) -> RetryPolicy:
        if self.max_attempts < 1:
            raise InvalidPolicyError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay_seconds <= 0:
            raise InvalidPolicyError("RetryPolicy.base_delay_seconds must be > 0")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise InvalidPolicyError(
                "RetryPolicy.max_delay_seconds must be >= base_delay_seconds"
            )
        if not self.retry_on:
            raise InvalidPolicyError("RetryPolicy.retry_on must not be empty")
        if ErrorCategory.VALIDATION in self.retry_on:
            # Validation errors are, by definition, not transient: retrying the
            # same input against the same node can never produce a different
            # outcome, so allowing this would just burn attempts pointlessly.
            raise InvalidPolicyError("RetryPolicy.retry_on must not include VALIDATION")
        return self


class TimeoutPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    duration_seconds: int
    on_timeout: TimeoutAction

    @model_validator(mode="after")
    def _validate(self) -> TimeoutPolicy:
        if self.duration_seconds <= 0:
            raise InvalidPolicyError("TimeoutPolicy.duration_seconds must be > 0")
        return self


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_execution_duration_seconds: int | None = None
    on_node_failure: FailurePropagation = FailurePropagation.FAIL_EXECUTION

    @model_validator(mode="after")
    def _validate(self) -> ExecutionPolicy:
        if (
            self.max_execution_duration_seconds is not None
            and self.max_execution_duration_seconds <= 0
        ):
            raise InvalidPolicyError(
                "ExecutionPolicy.max_execution_duration_seconds must be > 0 when set"
            )
        return self
