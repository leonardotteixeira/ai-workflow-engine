"""Evaluation scenarios — Fase 12 §12.5.

Not a benchmark (no timing/scoring) and not a duplicate of the pytest suite
— this is a small, readable script that exercises the engine's core
reliability *invariants* end to end, in plain language, so someone auditing
the repository can see "does this actually work" without running the full
test suite. Every scenario is deterministic (Mock provider/tools, injected
clock) and runs fully offline.

Run with: python -m evals.run
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from workflow_engine.domain import (
    BackoffStrategy,
    ErrorCategory,
    Execution,
    ExecutionContext,
    ExecutionState,
    NodeType,
    RetryPolicy,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.domain.condition import ConditionLeaf
from workflow_engine.engine import ExecutionEngine, default_registry, replay
from workflow_engine.persistence import (
    ExecutionRepository,
    PersistentExecutionEngine,
    create_schema,
    create_sqlite_engine,
    find_orphaned_node_executions,
    mark_orphaned_as_failed,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str


def _pending(workflow: WorkflowDefinition, execution_id: str = "exec-1") -> Execution:
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


def scenario_linear_execution() -> ScenarioResult:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="t", type=NodeType.TRANSFORM, config={"set": {"risk_score": 91}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="t"),
        WorkflowEdge(id="e2", source_node_id="t", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="eval-linear", version=1, name="linear", nodes=nodes, edges=edges,
        start_node_id="start", created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))
    ok = (
        result.execution.state == ExecutionState.COMPLETED
        and result.execution.context.variables == {"risk_score": 91}
    )
    return ScenarioResult("linear execution", ok, f"final state={result.execution.state.value}")


def scenario_branching() -> ScenarioResult:
    high = ConditionLeaf(field="score", operator="gte", value=80)
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="classify", type=NodeType.TRANSFORM, config={"set": {"score": 90}}),
        WorkflowNode(id="route", type=NodeType.CONDITION),
        WorkflowNode(id="hi", type=NodeType.END),
        WorkflowNode(id="lo", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="classify"),
        WorkflowEdge(id="e2", source_node_id="classify", target_node_id="route"),
        WorkflowEdge(id="e3", source_node_id="route", target_node_id="hi", condition=high),
        WorkflowEdge(id="e4", source_node_id="route", target_node_id="lo"),
    ]
    workflow = WorkflowDefinition(
        id="eval-branch", version=1, name="branch", nodes=nodes, edges=edges,
        start_node_id="start", created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))
    took_high_branch = result.node_executions[-1].node_id == "hi"
    return ScenarioResult("branching", took_high_branch, "high-risk branch was taken as expected")


def scenario_failure() -> ScenarioResult:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="bad", type=NodeType.TRANSFORM, config={"set": "not-a-dict"}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="bad"),
        WorkflowEdge(id="e2", source_node_id="bad", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="eval-fail", version=1, name="fail", nodes=nodes, edges=edges,
        start_node_id="start", created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))
    ok = result.execution.state == ExecutionState.FAILED
    return ScenarioResult("node failure", ok, f"final state={result.execution.state.value}")


def scenario_retry() -> ScenarioResult:
    from workflow_engine.domain import NodeResult
    from workflow_engine.domain.node_execution import NodeExecutionError
    from workflow_engine.engine.node_executor import (
        EndNodeExecutor,
        NodeExecutorRegistry,
        StartNodeExecutor,
    )

    class FlakyOnce:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, node: object, context: object) -> NodeResult:
            self.calls += 1
            if self.calls == 1:
                return NodeResult(
                    status="failed",
                    error=NodeExecutionError(category=ErrorCategory.TRANSIENT, message="flaky"),
                )
            return NodeResult(status="completed", output={})

    policy = RetryPolicy(
        max_attempts=3, backoff=BackoffStrategy.FIXED, base_delay_seconds=1, max_delay_seconds=10
    )
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="flaky", type=NodeType.TOOL, retry_policy=policy),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="flaky"),
        WorkflowEdge(id="e2", source_node_id="flaky", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="eval-retry", version=1, name="retry", nodes=nodes, edges=edges,
        start_node_id="start", created_at=NOW,
    )
    registry = NodeExecutorRegistry()
    registry.register(NodeType.START, StartNodeExecutor())
    registry.register(NodeType.END, EndNodeExecutor())
    flaky = FlakyOnce()
    registry.register(NodeType.TOOL, flaky)

    engine = ExecutionEngine(workflow, registry, clock=lambda: NOW)
    result = engine.run(_pending(workflow))
    ok = result.execution.state == ExecutionState.COMPLETED and flaky.calls == 2
    detail = f"attempts={flaky.calls}, final={result.execution.state.value}"
    return ScenarioResult("retry", ok, detail)


def scenario_human_approval() -> ScenarioResult:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="approval", type=NodeType.HUMAN_APPROVAL),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="approval"),
        WorkflowEdge(id="e2", source_node_id="approval", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="eval-hitl", version=1, name="hitl", nodes=nodes, edges=edges,
        start_node_id="start", created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    waiting = engine.run(_pending(workflow))
    if waiting.execution.state != ExecutionState.WAITING or waiting.approval_request is None:
        return ScenarioResult("human approval", False, "did not reach WAITING as expected")
    resumed = engine.resume(
        waiting.execution, waiting.approval_request, decision="approved", resolved_by="evaluator"
    )
    ok = resumed.execution.state == ExecutionState.COMPLETED
    return ScenarioResult("human approval", ok, f"resumed to {resumed.execution.state.value}")


def scenario_recovery() -> ScenarioResult:
    from workflow_engine.domain import NodeExecution

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eval.db")
        db = create_sqlite_engine(db_path)
        create_schema(db)
        workflow = WorkflowDefinition(
            id="eval-recovery", version=1, name="recovery",
            nodes=[
                WorkflowNode(id="start", type=NodeType.START),
                WorkflowNode(id="slow", type=NodeType.TRANSFORM),
                WorkflowNode(id="end", type=NodeType.END),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="start", target_node_id="slow"),
                WorkflowEdge(id="e2", source_node_id="slow", target_node_id="end"),
            ],
            start_node_id="start", created_at=NOW,
        )
        with db.begin() as conn:
            ExecutionRepository(conn).insert(
                Execution(
                    id="crashed-exec", workflow_definition_id=workflow.id,
                    workflow_version=workflow.version, state=ExecutionState.RUNNING,
                    context=ExecutionContext(), current_node_ids=["slow"],
                    created_at=NOW, updated_at=NOW,
                )
            )
            from workflow_engine.persistence.repositories import NodeExecutionRepository

            stuck = NodeExecution(
                id="ne-stuck", execution_id="crashed-exec", node_id="slow",
                idempotency_key="crashed-exec:slow:1",
            ).start(now=NOW - timedelta(hours=1))
            NodeExecutionRepository(conn).insert(stuck)

        with db.begin() as conn:
            orphans = find_orphaned_node_executions(
                conn, now=NOW, heartbeat_timeout=timedelta(minutes=5)
            )
            for orphan in orphans:
                mark_orphaned_as_failed(conn, orphan, now=NOW)

        ok = len(orphans) == 1
        db.dispose()
        detail = f"found and marked {len(orphans)} orphaned node(s)"
        return ScenarioResult("crash recovery", ok, detail)


def scenario_replay() -> ScenarioResult:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="t", type=NodeType.TRANSFORM, config={"set": {"x": 42}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="t"),
        WorkflowEdge(id="e2", source_node_id="t", target_node_id="end"),
    ]
    workflow = WorkflowDefinition(
        id="eval-replay", version=1, name="replay", nodes=nodes, edges=edges,
        start_node_id="start", created_at=NOW,
    )
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))
    replayed = replay(result.events)
    ok = replayed.variables == result.execution.context.variables == {"x": 42}
    return ScenarioResult("replay reconstruction", ok, "replayed state matches live execution")


def scenario_idempotency() -> ScenarioResult:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eval2.db")
        db = create_sqlite_engine(db_path)
        create_schema(db)
        nodes = [
            WorkflowNode(id="start", type=NodeType.START),
            WorkflowNode(id="t", type=NodeType.TRANSFORM, config={"set": {"x": 1}}),
            WorkflowNode(id="end", type=NodeType.END),
        ]
        edges = [
            WorkflowEdge(id="e1", source_node_id="start", target_node_id="t"),
            WorkflowEdge(id="e2", source_node_id="t", target_node_id="end"),
        ]
        workflow = WorkflowDefinition(
            id="eval-idem", version=1, name="idem", nodes=nodes, edges=edges,
            start_node_id="start", created_at=NOW,
        )
        from workflow_engine.engine.errors import InvalidExecutionStateError
        from workflow_engine.persistence import WorkflowDefinitionRepository

        with db.begin() as conn:
            WorkflowDefinitionRepository(conn).save(workflow)

        engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
        persistent = PersistentExecutionEngine(db, engine)
        first = persistent.start(_pending(workflow))

        second_call_rejected = False
        try:
            persistent.run(first.execution.id)
        except InvalidExecutionStateError:
            second_call_rejected = True

        db.dispose()
        ok = first.execution.state == ExecutionState.COMPLETED and second_call_rejected
        return ScenarioResult(
            "duplicate resume rejected", ok, "second run() on a COMPLETED execution was rejected"
        )


SCENARIOS = [
    scenario_linear_execution,
    scenario_branching,
    scenario_failure,
    scenario_retry,
    scenario_human_approval,
    scenario_recovery,
    scenario_replay,
    scenario_idempotency,
]


def main() -> int:
    results = [scenario() for scenario in SCENARIOS]
    print("AI Workflow Engine - Evaluation Scenarios\n" + "=" * 42)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.name}: {r.detail}")
    failed = [r for r in results if not r.passed]
    print("=" * 42)
    print(f"{len(results) - len(failed)}/{len(results)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
