"""Fase 9 §9.7/§9.8/§9.9/§9.10 — structured logging + metrics.

`InMemoryMetricsSink` and a stdlib `logging` handler are used instead of a
real metrics backend/log aggregator — no heavy infra, per the brief's own
"não adicionar infraestrutura pesada."
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from workflow_engine.domain import Execution as ExecutionModel
from workflow_engine.domain import (
    ExecutionContext,
    ExecutionState,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from workflow_engine.engine import ExecutionEngine, default_registry
from workflow_engine.engine.observability import InMemoryMetricsSink, redact_payload

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _linear_workflow() -> WorkflowDefinition:
    nodes = [
        WorkflowNode(id="start", type=NodeType.START),
        WorkflowNode(id="t", type=NodeType.TRANSFORM, config={"set": {"x": 1}}),
        WorkflowNode(id="end", type=NodeType.END),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="start", target_node_id="t"),
        WorkflowEdge(id="e2", source_node_id="t", target_node_id="end"),
    ]
    return WorkflowDefinition(
        id="wf",
        version=1,
        name="wf",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )


def _pending(workflow: WorkflowDefinition) -> ExecutionModel:
    return ExecutionModel(
        id="exec-1",
        workflow_definition_id=workflow.id,
        workflow_version=workflow.version,
        state=ExecutionState.PENDING,
        context=ExecutionContext(),
        current_node_ids=[],
        created_at=NOW,
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def test_redact_payload_masks_secret_shaped_keys() -> None:
    payload = {"api_key": "sk-abc123", "prompt": "hello", "Authorization": "Bearer xyz"}
    redacted = redact_payload(payload)
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["Authorization"] == "***REDACTED***"
    assert redacted["prompt"] == "hello"  # untouched


def test_redact_payload_recurses_into_nested_dicts() -> None:
    payload = {"usage": {"total_tokens": 10}, "config": {"secret_token": "abc"}}
    redacted = redact_payload(payload)
    assert redacted["usage"] == {"total_tokens": 10}
    assert redacted["config"]["secret_token"] == "***REDACTED***"


def test_redact_payload_is_case_insensitive() -> None:
    payload = {"PASSWORD": "hunter2", "Secret": "x"}
    redacted = redact_payload(payload)
    assert redacted["PASSWORD"] == "***REDACTED***"
    assert redacted["Secret"] == "***REDACTED***"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_metrics_sink_counts_every_emitted_event() -> None:
    workflow = _linear_workflow()
    metrics = InMemoryMetricsSink()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW, metrics=metrics)
    result = engine.run(_pending(workflow))

    total_increments = sum(metrics.counts.values())
    assert total_increments == len(result.events)
    assert metrics.counts["workflow_engine.execution_started"] == 1
    assert metrics.counts["workflow_engine.execution_completed"] == 1


def test_default_metrics_sink_is_a_safe_noop() -> None:
    """No metrics sink supplied — must not raise, must not require configuration."""
    workflow = _linear_workflow()
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW)
    result = engine.run(_pending(workflow))
    assert result.execution.state == ExecutionState.COMPLETED


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def test_logger_receives_structured_extra_fields_for_every_event(caplog) -> None:
    workflow = _linear_workflow()
    logger = logging.getLogger("workflow_engine.test")
    engine = ExecutionEngine(workflow, default_registry(), clock=lambda: NOW, logger=logger)

    with caplog.at_level(logging.INFO, logger="workflow_engine.test"):
        result = engine.run(_pending(workflow))

    assert len(caplog.records) == len(result.events)
    first = caplog.records[0]
    assert first.execution_id == result.execution.id
    assert first.event_type == "execution_started"
    assert hasattr(first, "sequence")


def test_no_print_statements_in_engine_or_persistence_source() -> None:
    """Fase 9 §9.8: 'Evitar print() em código de produção.'"""
    import ast
    import pathlib

    import workflow_engine.engine as engine_pkg
    import workflow_engine.persistence as persistence_pkg

    for pkg in (engine_pkg, persistence_pkg):
        pkg_dir = pathlib.Path(pkg.__file__).parent  # type: ignore[arg-type]
        for path in pkg_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    raise AssertionError(f"print() found in {path}")
