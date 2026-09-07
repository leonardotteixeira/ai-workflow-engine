"""SQLite persistence layer — Fase 7.

Depends on `workflow_engine.domain` and `workflow_engine.engine`, never the
other way around: neither the domain nor the engine imports anything from
here. This is the outermost layer that actually knows about SQLAlchemy/SQLite.
"""

from workflow_engine.persistence.db import create_schema, create_sqlite_engine
from workflow_engine.persistence.errors import (
    ConcurrencyConflictError,
    NotFoundError,
    PersistenceError,
)
from workflow_engine.persistence.persistent_engine import PersistentExecutionEngine
from workflow_engine.persistence.recovery import (
    OrphanedNodeExecution,
    find_orphaned_node_executions,
    mark_orphaned_as_failed,
)
from workflow_engine.persistence.repositories import (
    ApprovalRepository,
    EventRepository,
    ExecutionRepository,
    IdempotencyRepository,
    NodeExecutionRepository,
    WorkflowDefinitionRepository,
)

__all__ = [
    "create_schema",
    "create_sqlite_engine",
    "ConcurrencyConflictError",
    "NotFoundError",
    "PersistenceError",
    "PersistentExecutionEngine",
    "OrphanedNodeExecution",
    "find_orphaned_node_executions",
    "mark_orphaned_as_failed",
    "ApprovalRepository",
    "EventRepository",
    "ExecutionRepository",
    "IdempotencyRepository",
    "NodeExecutionRepository",
    "WorkflowDefinitionRepository",
]
