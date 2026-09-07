"""Engine-layer errors.

Distinct from `workflow_engine.domain.errors`: these are raised by the
orchestration layer itself (a missing executor, a runaway loop, an
unsatisfiable branch) — programmer/configuration errors that the Engine
refuses to silently turn into a FAILED execution, versus a node's own runtime
failure (which is legitimately represented as `Execution.state == FAILED` via
`NodeResult(status="failed")`).
"""

from __future__ import annotations


class EngineError(Exception):
    """Base class for all engine-layer errors."""


class UnknownNodeExecutorError(EngineError):
    """Raised when the registry has no NodeExecutor registered for a NodeType
    present in the workflow being run. This is a configuration error (the
    Engine wasn't wired up for this workflow), not a node runtime failure.
    """


class UnknownToolError(EngineError):
    """Raised when a ToolNode references a tool name not present in the
    ToolRegistry. Same category as UnknownNodeExecutorError: a workflow
    authoring/configuration problem, not a tool's own runtime failure (which
    is represented as `ToolResult(status="error")` instead).
    """


class NoMatchingBranchError(EngineError):
    """Raised when a CONDITION node's outgoing edges are evaluated against the
    current context and none match, and there is no default (unconditioned)
    edge to fall back to. Graph validation (DESIGN.md §4.1 rule 5) guarantees
    the *shape* is valid (>=2 edges, <=1 default) but cannot guarantee at
    validation time that some edge will match at runtime.
    """


class DefensiveLoopLimitExceededError(EngineError):
    """Raised if the execution loop runs more steps than the workflow has
    nodes. V1 graphs are validated as acyclic (DESIGN.md §4), so this should
    be unreachable for any `WorkflowDefinition` built through its normal
    constructor — this guard exists only for the defensive case of a graph
    assembled via `model_construct` (bypassing validation), so a bug or a
    deliberately malformed graph fails loudly instead of hanging.
    """


class InvalidExecutionStateError(EngineError):
    """Raised when `ExecutionEngine.run()` is called on an Execution that is
    not PENDING or RUNNING (e.g. WAITING, or a terminal state) — resuming a
    WAITING execution is a distinct, not-yet-implemented operation (see
    DESIGN.md roadmap / Fase 3 limitations), not something `run()` does
    implicitly.
    """
