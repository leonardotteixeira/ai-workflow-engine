from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workflow_engine.domain import (
    ExecutionContext,
    ExecutionState,
    NodeType,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.domain.workflow import WorkflowDefinition
from workflow_engine.engine import (
    ExecutionEngine,
    LLMNodeExecutor,
    LLMResponse,
    MockLLMProvider,
    ProviderError,
    TokenUsage,
    ToolNodeExecutor,
    UnknownToolError,
    default_tool_registry,
    registry_with_ai,
)
from workflow_engine.engine.errors import UnknownNodeExecutorError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _usage() -> TokenUsage:
    return TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10)


# ---------------------------------------------------------------------------
# LLMNodeExecutor (unit)
# ---------------------------------------------------------------------------


def test_llm_node_executor_success_stores_content_under_node_id() -> None:
    provider = MockLLMProvider({"say hi": LLMResponse(content="hello!", usage=_usage())})
    executor = LLMNodeExecutor(provider)
    node = WorkflowNode(id="greet", type=NodeType.LLM, config={"prompt": "say hi"})

    result = executor.execute(node, ExecutionContext())

    assert result.status == "completed"
    assert result.context_patch == {"greet": "hello!"}
    assert result.metadata["usage"]["total_tokens"] == 10


def test_llm_node_executor_missing_prompt_fails_validation() -> None:
    executor = LLMNodeExecutor(MockLLMProvider({}))
    node = WorkflowNode(id="greet", type=NodeType.LLM, config={})
    result = executor.execute(node, ExecutionContext())
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == ErrorCategory.VALIDATION


def test_llm_node_executor_translates_provider_error() -> None:
    provider = MockLLMProvider(
        {"boom": ProviderError(category=ErrorCategory.TRANSIENT, message="simulated timeout")}
    )
    executor = LLMNodeExecutor(provider)
    node = WorkflowNode(id="n1", type=NodeType.LLM, config={"prompt": "boom"})
    result = executor.execute(node, ExecutionContext())
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == ErrorCategory.TRANSIENT
    assert "timeout" in result.error.message


def test_llm_node_executor_handles_malformed_structured_response() -> None:
    """A provider returning a dict when the workflow expected a plain string
    is not an error by itself in V1 (no schema is enforced) — this documents
    that behavior rather than asserting a validation we don't implement."""
    provider = MockLLMProvider({"p": LLMResponse(content={"unexpected": "shape"}, usage=_usage())})
    executor = LLMNodeExecutor(provider)
    node = WorkflowNode(id="n1", type=NodeType.LLM, config={"prompt": "p"})
    result = executor.execute(node, ExecutionContext())
    assert result.status == "completed"
    assert result.output == {"content": {"unexpected": "shape"}}


# ---------------------------------------------------------------------------
# ToolNodeExecutor (unit)
# ---------------------------------------------------------------------------


def test_tool_node_executor_success() -> None:
    executor = ToolNodeExecutor(default_tool_registry())
    node = WorkflowNode(
        id="calc",
        type=NodeType.TOOL,
        config={"tool_name": "calculator", "input": {"op": "add", "a": 2, "b": 3}},
    )
    result = executor.execute(node, ExecutionContext())
    assert result.status == "completed"
    assert result.context_patch == {"calc": {"result": 5}}


def test_tool_node_executor_unknown_tool_propagates() -> None:
    executor = ToolNodeExecutor(default_tool_registry())
    node = WorkflowNode(
        id="calc", type=NodeType.TOOL, config={"tool_name": "ghost-tool", "input": {}}
    )
    with pytest.raises(UnknownToolError):
        executor.execute(node, ExecutionContext())


def test_tool_node_executor_tool_runtime_error_becomes_failed_result() -> None:
    executor = ToolNodeExecutor(default_tool_registry())
    node = WorkflowNode(
        id="calc",
        type=NodeType.TOOL,
        config={"tool_name": "calculator", "input": {"op": "divide", "a": 1, "b": 0}},
    )
    result = executor.execute(node, ExecutionContext())
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == ErrorCategory.VALIDATION


def test_tool_node_executor_non_dict_input_fails_validation() -> None:
    executor = ToolNodeExecutor(default_tool_registry())
    node = WorkflowNode(
        id="calc", type=NodeType.TOOL, config={"tool_name": "calculator", "input": "not-a-dict"}
    )
    result = executor.execute(node, ExecutionContext())
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == ErrorCategory.VALIDATION


def test_tool_node_executor_missing_tool_name_fails_validation() -> None:
    executor = ToolNodeExecutor(default_tool_registry())
    node = WorkflowNode(id="calc", type=NodeType.TOOL, config={})
    result = executor.execute(node, ExecutionContext())
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.category == ErrorCategory.VALIDATION


# ---------------------------------------------------------------------------
# Integration: START -> LLM -> TOOL -> END through the real Engine
# ---------------------------------------------------------------------------


def _ai_workflow() -> WorkflowDefinition:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="ask", type=NodeType.LLM, config={"prompt": "what is 2 plus 3?"}),
        WorkflowNode(
            id="compute",
            type=NodeType.TOOL,
            config={"tool_name": "calculator", "input": {"op": "add", "a": 2, "b": 3}},
        ),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="ask"),
        WorkflowEdge(id="e2", source_node_id="ask", target_node_id="compute"),
        WorkflowEdge(id="e3", source_node_id="compute", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-ai",
        version=1,
        name="ai-workflow",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def _make_execution(workflow: WorkflowDefinition):
    from workflow_engine.domain import Execution

    return Execution(
        id="exec-ai",
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


def test_full_llm_then_tool_workflow_completes() -> None:
    workflow = _ai_workflow()
    provider = MockLLMProvider(
        {"what is 2 plus 3?": LLMResponse(content="It's 5", usage=_usage())}
    )
    registry = registry_with_ai(provider, default_tool_registry())
    engine = ExecutionEngine(workflow, registry, clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.COMPLETED
    assert result.execution.context.variables == {
        "ask": "It's 5",
        "compute": {"result": 5},
    }
    assert [ne.node_id for ne in result.node_executions] == ["start", "ask", "compute", "end"]


def test_llm_failure_stops_the_workflow_before_the_tool_runs() -> None:
    workflow = _ai_workflow()
    provider = MockLLMProvider(
        {"what is 2 plus 3?": ProviderError(category=ErrorCategory.PERMANENT, message="bad prompt")}
    )
    registry = registry_with_ai(provider, default_tool_registry())
    engine = ExecutionEngine(workflow, registry, clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.FAILED
    assert [ne.node_id for ne in result.node_executions] == ["start", "ask"]


def test_tool_failure_stops_the_workflow_at_the_tool_node() -> None:
    workflow = _ai_workflow()
    # calculator receives a bad op via a hand-modified config
    broken_config = {"tool_name": "calculator", "input": {"op": "divide", "a": 1, "b": 0}}
    broken_nodes = [
        n if n.id != "compute" else n.model_copy(update={"config": broken_config})
        for n in workflow.nodes
    ]
    workflow = workflow.model_copy(update={"nodes": broken_nodes})
    provider = MockLLMProvider({"what is 2 plus 3?": LLMResponse(content="5", usage=_usage())})
    registry = registry_with_ai(provider, default_tool_registry())
    engine = ExecutionEngine(workflow, registry, clock=lambda: NOW)

    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.FAILED
    assert [ne.node_id for ne in result.node_executions] == ["start", "ask", "compute"]


def test_unknown_tool_referenced_by_workflow_propagates_from_engine_run() -> None:
    workflow = _ai_workflow()
    broken_config = {"tool_name": "ghost", "input": {}}
    broken_nodes = [
        n if n.id != "compute" else n.model_copy(update={"config": broken_config})
        for n in workflow.nodes
    ]
    workflow = workflow.model_copy(update={"nodes": broken_nodes})
    provider = MockLLMProvider({"what is 2 plus 3?": LLMResponse(content="5", usage=_usage())})
    registry = registry_with_ai(provider, default_tool_registry())
    engine = ExecutionEngine(workflow, registry, clock=lambda: NOW)

    with pytest.raises(UnknownToolError):
        engine.run(_make_execution(workflow))


def test_llm_or_tool_node_without_ai_registry_raises_unknown_executor() -> None:
    """Using default_registry() (no AI wiring) against a workflow with an LLM
    node fails loudly — Fase 3's guarantee still holds in Fase 4."""
    from workflow_engine.engine import default_registry

    workflow = _ai_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    with pytest.raises(UnknownNodeExecutorError):
        engine.run(_make_execution(workflow))
