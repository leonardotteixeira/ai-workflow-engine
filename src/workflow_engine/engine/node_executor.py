"""NodeExecutor contract + registry — DESIGN.md §6.

The Engine never branches on `node.type` (`if node.type == "llm": ... elif ...`)
— it asks the registry "which executor runs this node type?" and calls it
polymorphically. This is what lets Fase 4 add LLM/Tool executors without
touching the Engine at all.

`NodeExecutor.execute` mirrors the Fase 1 NodeResult contract: a node never
touches `ExecutionContext` directly, it returns `NodeResult.context_patch`
and the Engine (engine.py) decides how/when to merge it.
"""

from __future__ import annotations

from typing import Protocol

from workflow_engine.domain import EventType, ExecutionContext, NodeResult, WorkflowNode
from workflow_engine.domain.enums import ErrorCategory, NodeType
from workflow_engine.domain.event import EventDraft
from workflow_engine.domain.node_execution import NodeExecutionError
from workflow_engine.engine.errors import UnknownNodeExecutorError
from workflow_engine.engine.providers import LLMMessage, LLMProvider, LLMRequest, ProviderError
from workflow_engine.engine.tools import ToolRegistry


class NodeExecutor(Protocol):
    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult: ...


class NodeExecutorRegistry:
    """A plain lookup table from NodeType to NodeExecutor. Not a DI framework —
    just enough indirection to keep the Engine's dispatch logic to one
    `dict.get` instead of a chain of `if/elif`.
    """

    def __init__(self) -> None:
        self._executors: dict[NodeType, NodeExecutor] = {}

    def register(self, node_type: NodeType, executor: NodeExecutor) -> None:
        self._executors[node_type] = executor

    def resolve(self, node_type: NodeType) -> NodeExecutor:
        executor = self._executors.get(node_type)
        if executor is None:
            raise UnknownNodeExecutorError(
                f"no NodeExecutor registered for node type {node_type.value!r}"
            )
        return executor


class StartNodeExecutor:
    """START is a pure marker — it does no work and produces no output."""

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        return NodeResult(status="completed", output={})


class EndNodeExecutor:
    """END is a pure marker — reaching it is what makes the Engine finish."""

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        return NodeResult(status="completed", output={})


class TransformNodeExecutor:
    """A deterministic, config-driven data transform: `node.config["set"]` is
    merged into the context verbatim (and returned as output). No expression
    language, no eval — V1 transforms are static key/value patches, which is
    enough to demonstrate context flow without reintroducing the arbitrary-
    code-execution risk the condition DSL was designed to avoid (DESIGN.md §5).
    """

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        patch = node.config.get("set", {})
        if not isinstance(patch, dict):
            return NodeResult(
                status="failed",
                error=NodeExecutionError(
                    category=ErrorCategory.VALIDATION,
                    message=(
                        f"TRANSFORM node {node.id!r} config['set'] must be a dict, "
                        f"got {type(patch).__name__}"
                    ),
                ),
            )
        return NodeResult(status="completed", output=dict(patch), context_patch=dict(patch))


class ConditionNodeExecutor:
    """CONDITION nodes do no work themselves — branch selection happens in
    `next_node.resolve_next_node_id`, evaluated against the context this node
    receives unchanged. Kept separate from execution (DESIGN.md §6, Fase 3
    brief 3.6) so the Engine's node-dispatch loop and its branch-resolution
    logic stay independently testable.
    """

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        return NodeResult(status="completed", output={})


class HumanApprovalNodeExecutor:
    """Always returns `waiting` — DESIGN.md §9: reaching this node suspends
    the execution until an external approve/reject decision arrives. Fase 3
    only implements the suspend half (Engine -> WAITING); creating the
    `ApprovalRequest` record and resuming after a decision is HITL runtime
    wiring, deferred to a later phase (see engine.py module docstring).
    """

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        return NodeResult(
            status="waiting",
            events=[EventDraft(event_type=EventType.APPROVAL_REQUESTED, node_id=node.id)],
        )


class LLMNodeExecutor:
    """Wraps a single injected `LLMProvider` (DESIGN.md §7) — the Engine never
    imports a concrete provider SDK, only this executor does, and only
    through the `LLMProvider` protocol. `node.config["prompt"]` is a static
    string in V1 (no templating language over prior node outputs yet — that
    would need a small, still-eval-free templating mechanism, deferred).

    The LLM's raw text/dict response is stored under the node's own id in the
    context (`context_patch = {node.id: response.content}`), the same
    convention `TransformNodeExecutor` and `ToolNodeExecutor` use, so
    downstream CONDITION nodes can reference it as `f"{node_id}.field"`.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        prompt = node.config.get("prompt", "")
        if not isinstance(prompt, str) or not prompt:
            return NodeResult(
                status="failed",
                error=NodeExecutionError(
                    category=ErrorCategory.VALIDATION,
                    message=f"LLM node {node.id!r} requires a non-empty config['prompt'] string",
                ),
            )

        request = LLMRequest(messages=[LLMMessage(role="user", content=prompt)])
        try:
            response = self._provider.generate(request)
        except ProviderError as error:
            return NodeResult(
                status="failed",
                error=NodeExecutionError(category=error.category, message=error.message),
            )

        return NodeResult(
            status="completed",
            output={"content": response.content},
            context_patch={node.id: response.content},
            metadata={"usage": response.usage.model_dump()},
        )


class ToolNodeExecutor:
    """Wraps a `ToolRegistry` (DESIGN.md §8). `node.config["tool_name"]`
    selects the tool; `node.config["input"]` (a static dict in V1, same
    limitation as LLMNodeExecutor's prompt) is passed through unchanged — a
    Tool never sees the Execution or its context (DESIGN.md §8).

    An unregistered `tool_name` raises `UnknownToolError`, which propagates
    out of the Engine's run loop uncaught, the same way an unregistered
    `NodeType` does via `UnknownNodeExecutorError` — both are workflow/wiring
    configuration problems, not a tool's own runtime failure (that case is
    `ToolResult(status="error")`, translated below into a failed NodeResult).
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry

    def execute(self, node: WorkflowNode, context: ExecutionContext) -> NodeResult:
        tool_name = node.config.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return NodeResult(
                status="failed",
                error=NodeExecutionError(
                    category=ErrorCategory.VALIDATION,
                    message=(
                        f"TOOL node {node.id!r} requires a non-empty config['tool_name'] string"
                    ),
                ),
            )
        tool_input = node.config.get("input", {})
        if not isinstance(tool_input, dict):
            return NodeResult(
                status="failed",
                error=NodeExecutionError(
                    category=ErrorCategory.VALIDATION,
                    message=f"TOOL node {node.id!r} config['input'] must be a dict",
                ),
            )

        tool = self._tool_registry.resolve(tool_name)  # UnknownToolError propagates uncaught
        result = tool.execute(tool_input)

        if result.status == "success":
            output = result.output or {}
            return NodeResult(
                status="completed", output=output, context_patch={node.id: output}
            )

        assert result.error is not None  # ToolResult invariant: error set iff status == "error"
        return NodeResult(
            status="failed",
            error=NodeExecutionError(category=result.error.category, message=result.error.message),
        )


def default_registry() -> NodeExecutorRegistry:
    """Registry pre-populated with the structural node executors every
    workflow needs (START/END/CONDITION/TRANSFORM/HUMAN_APPROVAL). LLM and
    TOOL are intentionally left unregistered here — Fase 4 registers those
    explicitly, so a workflow using them before Fase 4's providers exist fails
    loudly via UnknownNodeExecutorError rather than silently no-op'ing.
    """
    registry = NodeExecutorRegistry()
    registry.register(NodeType.START, StartNodeExecutor())
    registry.register(NodeType.END, EndNodeExecutor())
    registry.register(NodeType.CONDITION, ConditionNodeExecutor())
    registry.register(NodeType.TRANSFORM, TransformNodeExecutor())
    registry.register(NodeType.HUMAN_APPROVAL, HumanApprovalNodeExecutor())
    return registry


def registry_with_ai(
    llm_provider: LLMProvider, tool_registry: ToolRegistry
) -> NodeExecutorRegistry:
    """`default_registry()` plus LLM/TOOL executors wired to the given
    provider/tool registry. Kept as an explicit, separate builder (rather than
    always wiring LLM/TOOL into `default_registry()`) so a workflow with no
    AI/tool nodes never needs to construct a provider or tool registry at all.
    """
    registry = default_registry()
    registry.register(NodeType.LLM, LLMNodeExecutor(llm_provider))
    registry.register(NodeType.TOOL, ToolNodeExecutor(tool_registry))
    return registry
