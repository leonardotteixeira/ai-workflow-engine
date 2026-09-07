"""Builder helpers for Fase 3 (Engine) tests. Not part of the domain/engine package."""

from __future__ import annotations

from datetime import UTC, datetime

from workflow_engine.domain import (
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.condition import ConditionLeaf

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def linear_transform_workflow() -> WorkflowDefinition:
    """START -> analyze (sets risk_score=87) -> END."""
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="analyze", type=NodeType.TRANSFORM, config={"set": {"risk_score": 87}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="analyze"),
        WorkflowEdge(id="e2", source_node_id="analyze", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-linear",
        version=1,
        name="linear-transform",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def three_step_workflow() -> WorkflowDefinition:
    """START -> a -> b -> c -> END, each a TRANSFORM tagging its own visit."""
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="a", type=NodeType.TRANSFORM, config={"set": {"a": True}}),
        WorkflowNode(id="b", type=NodeType.TRANSFORM, config={"set": {"b": True}}),
        WorkflowNode(id="c", type=NodeType.TRANSFORM, config={"set": {"c": True}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="a"),
        WorkflowEdge(id="e2", source_node_id="a", target_node_id="b"),
        WorkflowEdge(id="e3", source_node_id="b", target_node_id="c"),
        WorkflowEdge(id="e4", source_node_id="c", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-three-step",
        version=1,
        name="three-step",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def branching_risk_workflow(risk_score: int = 90) -> WorkflowDefinition:
    """START -> classify(sets risk_score) -> CONDITION -> {HIGH: approval, LOW: end_low}."""
    high = ConditionLeaf(field="risk_score", operator="gte", value=80)
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(
            id="classify", type=NodeType.TRANSFORM, config={"set": {"risk_score": risk_score}}
        ),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="end_high", type=NodeType.END),
        WorkflowNode(id="end_low", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="classify"),
        WorkflowEdge(id="e2", source_node_id="classify", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="approval", condition=high),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="end_low"),
        WorkflowEdge(id="e5", source_node_id="approval", target_node_id="end_high"),
    ]
    return WorkflowDefinition(
        id="wf-branch",
        version=1,
        name="branching-risk",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def failing_workflow() -> WorkflowDefinition:
    """START -> bad (config['set'] is not a dict, so TransformNodeExecutor fails) -> END."""
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="bad", type=NodeType.TRANSFORM, config={"set": "not-a-dict"}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="bad"),
        WorkflowEdge(id="e2", source_node_id="bad", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-failing",
        version=1,
        name="failing",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def llm_placeholder_workflow() -> WorkflowDefinition:
    """START -> llm_step (NodeType.LLM, unregistered until Fase 4) -> END."""
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="llm_step", type=NodeType.LLM),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="llm_step"),
        WorkflowEdge(id="e2", source_node_id="llm_step", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf-llm-placeholder",
        version=1,
        name="llm-placeholder",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def make_pending_execution(
    workflow: WorkflowDefinition, *, execution_id: str = "exec-1"
) -> Execution:
    return Execution(
        id=execution_id,
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )
