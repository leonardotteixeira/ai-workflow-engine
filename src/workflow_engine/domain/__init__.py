"""Pure domain layer — Fase 1.

No I/O, no framework, no infrastructure. Everything importable from this
package is a plain data model (Pydantic) or a pure function operating on
those models. See DESIGN.md for the full architecture this implements.
"""

from workflow_engine.domain.approval import ApprovalRequest, approve, reject
from workflow_engine.domain.condition import (
    ConditionAll,
    ConditionAny,
    ConditionExpr,
    ConditionLeaf,
    evaluate,
)
from workflow_engine.domain.enums import (
    ApprovalStatus,
    BackoffStrategy,
    ConditionOperator,
    ErrorCategory,
    EventType,
    ExecutionState,
    FailurePropagation,
    NodeExecutionStatus,
    NodeType,
    TimeoutAction,
)
from workflow_engine.domain.errors import (
    DomainError,
    DuplicateNodeError,
    InvalidApprovalError,
    InvalidEdgeError,
    InvalidPolicyError,
    InvalidStateTransitionError,
    InvalidWorkflowError,
)
from workflow_engine.domain.event import Event, EventDraft
from workflow_engine.domain.execution import Execution, ExecutionContext
from workflow_engine.domain.node_execution import NodeExecution, NodeExecutionError
from workflow_engine.domain.node_result import NodeResult
from workflow_engine.domain.policies import ExecutionPolicy, RetryPolicy, TimeoutPolicy
from workflow_engine.domain.workflow import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    validate_graph,
)

__all__ = [
    "ApprovalRequest",
    "approve",
    "reject",
    "ConditionAll",
    "ConditionAny",
    "ConditionExpr",
    "ConditionLeaf",
    "evaluate",
    "ApprovalStatus",
    "BackoffStrategy",
    "ConditionOperator",
    "ErrorCategory",
    "EventType",
    "ExecutionState",
    "FailurePropagation",
    "NodeExecutionStatus",
    "NodeType",
    "TimeoutAction",
    "DomainError",
    "DuplicateNodeError",
    "InvalidApprovalError",
    "InvalidEdgeError",
    "InvalidPolicyError",
    "InvalidStateTransitionError",
    "InvalidWorkflowError",
    "Event",
    "EventDraft",
    "Execution",
    "ExecutionContext",
    "NodeExecution",
    "NodeExecutionError",
    "NodeResult",
    "ExecutionPolicy",
    "RetryPolicy",
    "TimeoutPolicy",
    "WorkflowDefinition",
    "WorkflowEdge",
    "WorkflowNode",
    "validate_graph",
]
