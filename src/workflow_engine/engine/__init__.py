"""In-memory execution engine — Fase 3.

Orchestrates the pure domain layer (`workflow_engine.domain`) against a
`WorkflowDefinition`. Depends only on the domain and the standard library —
no persistence, no HTTP, no LLM SDK. See `engine.py` for what is and isn't
implemented yet.
"""

from workflow_engine.engine.engine import Decision, EngineRunResult, ExecutionEngine
from workflow_engine.engine.errors import (
    DefensiveLoopLimitExceededError,
    EngineError,
    InvalidExecutionStateError,
    NoMatchingBranchError,
    UnknownNodeExecutorError,
    UnknownToolError,
)
from workflow_engine.engine.node_executor import (
    ConditionNodeExecutor,
    EndNodeExecutor,
    HumanApprovalNodeExecutor,
    LLMNodeExecutor,
    NodeExecutor,
    NodeExecutorRegistry,
    StartNodeExecutor,
    ToolNodeExecutor,
    TransformNodeExecutor,
    default_registry,
    registry_with_ai,
)
from workflow_engine.engine.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    MockLLMProvider,
    ProviderError,
    TokenUsage,
)
from workflow_engine.engine.replay import (
    NodeHistoryEntry,
    ReplayedExecution,
    ReplayError,
    replay,
    validate_event_sequence,
)
from workflow_engine.engine.tools import (
    CalculatorTool,
    TextLengthTool,
    Tool,
    ToolExecutionError,
    ToolRegistry,
    ToolResult,
    default_tool_registry,
)

__all__ = [
    "Decision",
    "EngineRunResult",
    "ExecutionEngine",
    "DefensiveLoopLimitExceededError",
    "EngineError",
    "InvalidExecutionStateError",
    "NoMatchingBranchError",
    "UnknownNodeExecutorError",
    "UnknownToolError",
    "ConditionNodeExecutor",
    "EndNodeExecutor",
    "HumanApprovalNodeExecutor",
    "LLMNodeExecutor",
    "NodeExecutor",
    "NodeExecutorRegistry",
    "StartNodeExecutor",
    "ToolNodeExecutor",
    "TransformNodeExecutor",
    "default_registry",
    "registry_with_ai",
    "NodeHistoryEntry",
    "ReplayedExecution",
    "ReplayError",
    "replay",
    "validate_event_sequence",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "MockLLMProvider",
    "ProviderError",
    "TokenUsage",
    "CalculatorTool",
    "TextLengthTool",
    "Tool",
    "ToolExecutionError",
    "ToolRegistry",
    "ToolResult",
    "default_tool_registry",
]
