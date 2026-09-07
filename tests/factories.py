"""Small builder helpers shared across tests. Not part of the domain package."""

from __future__ import annotations

from datetime import UTC, datetime

from workflow_engine.domain import (
    ApprovalRequest,
    ApprovalStatus,
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeExecution,
    NodeExecutionStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_node(node_id: str, node_type: NodeType, **kwargs: object) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, **kwargs)  # type: ignore[arg-type]


def make_edge(edge_id: str, source: str, target: str, condition: object = None) -> WorkflowEdge:
    return WorkflowEdge(  # type: ignore[arg-type]
        id=edge_id, source_node_id=source, target_node_id=target, condition=condition
    )


def linear_workflow(workflow_id: str = "wf-1", version: int = 1) -> WorkflowDefinition:
    """START -> TRANSFORM -> END, the simplest valid graph."""
    nodes = [
        make_node("start", NodeType.START),
        make_node("transform", NodeType.TRANSFORM),
        make_node("end", NodeType.END),
    ]
    edges = [
        make_edge("e1", "start", "transform"),
        make_edge("e2", "transform", "end"),
    ]
    return WorkflowDefinition(
        id=workflow_id,
        version=version,
        name="linear",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def branching_workflow() -> WorkflowDefinition:
    """START -> CONDITION -> {HIGH: HUMAN_APPROVAL -> END, LOW: END}."""
    nodes = [
        make_node("start", NodeType.START),
        make_node("classify", NodeType.CONDITION),
        make_node("approval", NodeType.HUMAN_APPROVAL),
        make_node("end_high", NodeType.END),
        make_node("end_low", NodeType.END),
    ]
    from workflow_engine.domain.condition import ConditionLeaf

    high = ConditionLeaf(field="risk_score", operator="gte", value=80)
    edges = [
        make_edge("e1", "start", "classify"),
        make_edge("e2", "classify", "approval", condition=high),
        make_edge("e3", "classify", "end_low"),  # default branch
        make_edge("e4", "approval", "end_high"),
    ]
    return WorkflowDefinition(
        id="wf-branch",
        version=1,
        name="branching",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def make_execution(
    *,
    state: ExecutionState = ExecutionState.PENDING,
    execution_id: str = "exec-1",
    workflow_definition_id: str = "wf-1",
) -> Execution:
    return Execution(
        id=execution_id,
        workflow_definition_id=workflow_definition_id,
        workflow_version=1,
        state=state,
        context=ExecutionContext(trigger_input={"foo": "bar"}),
        current_node_ids=["start"],
        created_at=NOW,
        updated_at=NOW,
    )


def make_node_execution(
    *, status: NodeExecutionStatus = NodeExecutionStatus.PENDING, node_id: str = "n1"
) -> NodeExecution:
    return NodeExecution(
        id="ne-1",
        execution_id="exec-1",
        node_id=node_id,
        idempotency_key="key-1",
        status=status,
    )


def make_approval(
    *, status: ApprovalStatus = ApprovalStatus.PENDING, execution_id: str = "exec-1"
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id="appr-1",
        execution_id=execution_id,
        node_id="approval",
        node_execution_id="ne-1",
        status=status,
        requested_at=NOW,
    )
