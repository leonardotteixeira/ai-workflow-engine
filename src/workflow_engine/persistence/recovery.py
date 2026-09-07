"""Crash recovery scan — DESIGN.md §12, Fase 7 §7.7/§7.8.

`scan_for_orphaned_node_executions` is the recovery mechanism DESIGN.md §12.2
describes: every state transition is already persisted atomically with its
cause (that's what `PersistentExecutionEngine`'s transaction boundary
guarantees), so "checkpointing" is just the normal persisted state — there is
no separate checkpoint file or WAL replay to write. Recovery only has to find
work that was interrupted mid-flight:

- `Execution.state == WAITING`: not a failure. A human hasn't acted yet; the
  execution is exactly where it should be. No action.
- `Execution.state == RUNNING` whose latest `NodeExecution` is `RUNNING` and
  started longer than `heartbeat_timeout` ago: the process almost certainly
  died while running that node. This scan marks that NodeExecution `FAILED`
  (category=TRANSIENT) — it does NOT retry it itself (DESIGN.md §12.2: retry
  policy application belongs to Fase 8's retry runtime, not to recovery) and
  it does NOT re-run the node blindly (a node whose side effect may have
  already happened must go through the same idempotency-key check any other
  retry would, which again is Fase 8's job).

This module never touches `Execution.state` directly — marking a
NodeExecution FAILED is a data correction, but only the retry runtime deciding
"no more attempts left" is entitled to fail the *Execution*; recovery leaving
`Execution.state == RUNNING` with a FAILED latest NodeExecution is the
correct, honest intermediate state until Fase 8 acts on it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection

from workflow_engine.domain import ExecutionState
from workflow_engine.domain.enums import ErrorCategory, NodeExecutionStatus
from workflow_engine.domain.node_execution import NodeExecutionError
from workflow_engine.persistence.repositories import ExecutionRepository, NodeExecutionRepository


@dataclass(frozen=True)
class OrphanedNodeExecution:
    execution_id: str
    node_execution_id: str
    node_id: str
    started_at: datetime


def find_orphaned_node_executions(
    conn: Connection, *, now: datetime, heartbeat_timeout: timedelta
) -> list[OrphanedNodeExecution]:
    """Read-only scan: identify RUNNING executions whose latest NodeExecution
    has been RUNNING longer than `heartbeat_timeout`. Does not mutate
    anything — see `mark_orphaned_as_failed` for the write side, kept
    separate so a caller can inspect/log findings before acting on them.
    """
    execution_repo = ExecutionRepository(conn)
    node_execution_repo = NodeExecutionRepository(conn)

    orphans: list[OrphanedNodeExecution] = []
    for execution in execution_repo.list_by_states([ExecutionState.RUNNING]):
        latest = node_execution_repo.get_latest_for_execution(execution.id)
        if latest is None or latest.status != NodeExecutionStatus.RUNNING:
            continue
        if latest.started_at is None:
            continue  # RUNNING without started_at would be a data bug, not this scan's concern
        if now - latest.started_at > heartbeat_timeout:
            orphans.append(
                OrphanedNodeExecution(
                    execution_id=execution.id,
                    node_execution_id=latest.id,
                    node_id=latest.node_id,
                    started_at=latest.started_at,
                )
            )
    return orphans


def mark_orphaned_as_failed(
    conn: Connection, orphan: OrphanedNodeExecution, *, now: datetime
) -> None:
    """Insert a new NodeExecution row recording the orphaned attempt as
    FAILED(TRANSIENT) — mirrors `NodeExecution.fail()`'s write-once contract
    (Fase 1): the original RUNNING row is never mutated, a terminal outcome
    for that attempt is appended instead, so the interrupted attempt's
    history stays intact for audit.
    """
    node_execution_repo = NodeExecutionRepository(conn)
    rows = node_execution_repo.list_by_execution(orphan.execution_id)
    original = next(row for row in rows if row.id == orphan.node_execution_id)

    failed = original.fail(
        NodeExecutionError(
            category=ErrorCategory.TRANSIENT,
            message=(
                f"orphaned: node {orphan.node_id!r} was RUNNING since "
                f"{orphan.started_at.isoformat()} with no completion recorded — "
                "the process that ran it likely crashed"
            ),
        ),
        now=now,
    )
    # A fresh id: this is a new row documenting the outcome of the orphaned
    # attempt, not a mutation of the original (write-once) RUNNING row.
    node_execution_repo.insert(failed.model_copy(update={"id": str(uuid.uuid4())}))
