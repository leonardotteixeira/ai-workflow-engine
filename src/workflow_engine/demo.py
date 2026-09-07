"""Standalone, offline demo — Fase 12 §12.14/§12.15.

Run with: python -m workflow_engine.demo

Builds and runs the workflow from the project brief end to end:

    START -> LLM (risk analysis) -> CONDITION
      - LOW risk  -> END
      - HIGH risk -> TOOL (calculator) -> HUMAN_APPROVAL -> CONDITION
                       - approved -> REPORT -> END
                       - rejected -> END

Uses `MockLLMProvider` with a canned response and `CalculatorTool` — fully
offline, no API key, no network call, deterministic every run (matches
DESIGN.md's "V1 Mock providers are fully offline" and Fase 4's decision not
to require credentials for demonstration). Persists to a temporary SQLite
file so the run also exercises `PersistentExecutionEngine`, not just the
in-memory `ExecutionEngine`.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

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
from workflow_engine.domain.enums import ConditionOperator
from workflow_engine.engine.engine import ExecutionEngine
from workflow_engine.engine.node_executor import registry_with_ai
from workflow_engine.engine.providers import LLMResponse, MockLLMProvider, TokenUsage
from workflow_engine.engine.replay import replay
from workflow_engine.engine.tools import default_tool_registry
from workflow_engine.persistence import (
    EventRepository,
    ExecutionRepository,
    PersistentExecutionEngine,
    WorkflowDefinitionRepository,
    create_schema,
    create_sqlite_engine,
)

ANALYSIS_PROMPT = "Assess the fraud risk of this transaction and return a risk score."


def _build_workflow() -> WorkflowDefinition:
    approved = ConditionLeaf(
        field="approval.decision", operator=ConditionOperator.EQ, value="approved"
    )
    high_risk = ConditionLeaf(field="ask.risk_score", operator=ConditionOperator.GTE, value=70)

    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="ask", type=NodeType.LLM, config={"prompt": ANALYSIS_PROMPT}),
        WorkflowNode(id="route_risk", type=NodeType.CONDITION),
        WorkflowNode(id="end_low", type=NodeType.END),
        WorkflowNode(
            id="surcharge",
            type=NodeType.TOOL,
            config={"tool_name": "calculator", "input": {"op": "multiply", "a": 1000, "b": 0.02}},
        ),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="route_decision", type=NodeType.CONDITION),
        WorkflowNode(id="report", type=NodeType.TRANSFORM, config={"set": {"reported": True}}),
        WorkflowNode(id="end_approved", type=NodeType.END),
        WorkflowNode(id="end_rejected", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="ask"),
        WorkflowEdge(id="e2", source_node_id="ask", target_node_id="route_risk"),
        WorkflowEdge(
            id="e3", source_node_id="route_risk", target_node_id="surcharge", condition=high_risk
        ),
        WorkflowEdge(id="e4", source_node_id="route_risk", target_node_id="end_low"),
        WorkflowEdge(id="e5", source_node_id="surcharge", target_node_id="approval"),
        WorkflowEdge(id="e6", source_node_id="approval", target_node_id="route_decision"),
        WorkflowEdge(
            id="e7", source_node_id="route_decision", target_node_id="report", condition=approved
        ),
        WorkflowEdge(id="e8", source_node_id="route_decision", target_node_id="end_rejected"),
        WorkflowEdge(id="e9", source_node_id="report", target_node_id="end_approved"),
    ]
    return WorkflowDefinition(
        id="demo-fraud-review",
        version=1,
        name="Fraud Review Demo",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=datetime.now(UTC),
    )


def main() -> None:
    print("AI Workflow Engine - offline demo (Mock LLM provider, no network, no API key)\n")

    provider = MockLLMProvider(
        {
            ANALYSIS_PROMPT: LLMResponse(
                content={"risk_score": 87, "reason": "unusual location + amount"},
                usage=TokenUsage(prompt_tokens=42, completion_tokens=18, total_tokens=60),
            )
        }
    )
    registry = registry_with_ai(provider, default_tool_registry())
    workflow = _build_workflow()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "demo.db")
        db = create_sqlite_engine(db_path)
        create_schema(db)
        with db.begin() as conn:
            WorkflowDefinitionRepository(conn).save(workflow)

        execution = Execution(
            id="demo-exec-1",
            workflow_definition_id=workflow.id,
            workflow_version=workflow.version,
            state=ExecutionState.PENDING,
            context=ExecutionContext(trigger_input={"transaction_id": "txn_00042"}),
            current_node_ids=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        persistent = PersistentExecutionEngine(db, ExecutionEngine(workflow, registry))
        result = persistent.start(execution)

        print(f"Execution {result.execution.id} -> {result.execution.state.value}")
        print(f"  context so far: {result.execution.context.variables}")

        if result.execution.state == ExecutionState.WAITING:
            assert result.approval_request is not None
            print(f"  waiting on Human Approval at node {result.approval_request.node_id!r}")
            print("  -> approving as 'demo-reviewer@example.com'...")
            resumed = persistent.resume(
                result.execution.id,
                result.approval_request.approval_id,
                decision="approved",
                resolved_by="demo-reviewer@example.com",
            )
            print(f"Execution {resumed.execution.id} -> {resumed.execution.state.value}")
            print(f"  final context: {resumed.execution.context.variables}")

        with db.begin() as conn:
            events = EventRepository(conn).list_by_execution(execution.id)
            final_execution = ExecutionRepository(conn).get(execution.id)

        print(f"\n{len(events)} events recorded:")
        for event in events:
            node = f" ({event.node_id})" if event.node_id else ""
            print(f"  {event.sequence:>2}. {event.event_type.value}{node}")

        replayed = replay(events)
        print("\nReplay (reconstructed purely from the event log, no LLM/tool calls):")
        print(f"  reconstructed state: {replayed.state.value if replayed.state else 'unknown'}")
        print(f"  reconstructed variables: {replayed.variables}")
        assert final_execution is not None
        matches = replayed.variables == final_execution.context.variables
        print(f"  matches live execution: {matches}")

        db.dispose()


if __name__ == "__main__":
    main()
