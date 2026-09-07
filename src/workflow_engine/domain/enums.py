"""Domain enums.

Plain `enum.StrEnum` subclasses — no behavior, no I/O.
"""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    START = "START"
    LLM = "LLM"
    TOOL = "TOOL"
    CONDITION = "CONDITION"
    TRANSFORM = "TRANSFORM"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    END = "END"


class ExecutionState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ErrorCategory(StrEnum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    VALIDATION = "VALIDATION"


class BackoffStrategy(StrEnum):
    FIXED = "FIXED"
    EXPONENTIAL = "EXPONENTIAL"


class TimeoutAction(StrEnum):
    FAIL = "FAIL"
    RETRY = "RETRY"


class FailurePropagation(StrEnum):
    """V1 supports a single strategy — kept as an enum (not a bare literal) so
    the roadmap can add strategies without changing the field's type.
    """

    FAIL_EXECUTION = "FAIL_EXECUTION"


class ConditionOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    EXISTS = "exists"


class EventType(StrEnum):
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_CANCELLED = "execution_cancelled"
    EXECUTION_RESUMED = "execution_resumed"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    NODE_RETRYING = "node_retrying"
    NODE_SKIPPED = "node_skipped"
    CONDITION_EVALUATED = "condition_evaluated"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
