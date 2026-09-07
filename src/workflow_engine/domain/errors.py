"""Domain-level errors.

These are the only exceptions the domain layer raises. None of them wrap
infrastructure concerns (no DB errors, no HTTP errors, no provider errors) —
this module has zero imports outside the standard library so it can never
become a dumping ground for infrastructure exception translation.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain-level errors."""


class InvalidStateTransitionError(DomainError):
    """Raised when a state transition is not allowed by the state machine."""

    def __init__(self, entity: str, current: str, target: str) -> None:
        self.entity = entity
        self.current = current
        self.target = target
        super().__init__(f"{entity}: invalid transition {current!r} -> {target!r}")


class InvalidWorkflowError(DomainError):
    """Raised when a WorkflowDefinition's graph is structurally invalid."""


class DuplicateNodeError(DomainError):
    """Raised when a WorkflowDefinition declares two nodes with the same id."""


class InvalidEdgeError(DomainError):
    """Raised when a WorkflowEdge references a non-existent node or violates
    edge-level structural rules (e.g. a ConditionNode without branching edges).
    """


class InvalidApprovalError(DomainError):
    """Raised when an ApprovalRequest transition is attempted from a non-pending
    state, or a duplicate/late decision is applied.
    """


class InvalidPolicyError(DomainError):
    """Raised when a RetryPolicy/TimeoutPolicy/ExecutionPolicy configuration is
    internally inconsistent (e.g. negative durations, impossible combinations).
    """
