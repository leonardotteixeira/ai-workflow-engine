"""SQLAlchemy Core table definitions — DESIGN.md §10.

Deliberately Core, not the ORM: repositories (repositories.py) translate
explicitly between rows and the frozen Pydantic domain models via
`model_dump_json()`/`model_validate_json()`. An ORM's identity map and
attribute-mutation model don't mix well with immutable domain objects, and
Core keeps the SQL fully visible instead of hidden behind ORM query-building
magic — which matters for a portfolio piece meant to demonstrate
understanding, not just usage.

SQLite only (DESIGN.md §1's justification stands unchanged): WAL mode is
configured in db.py, not here.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

workflow_definitions = Table(
    "workflow_definitions",
    metadata,
    Column("id", String, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("definition_json", String, nullable=False),
)

executions = Table(
    "executions",
    metadata,
    Column("id", String, primary_key=True),
    Column("workflow_definition_id", String, nullable=False),
    Column("workflow_version", Integer, nullable=False),
    Column("state", String, nullable=False),
    Column("context_json", String, nullable=False),
    Column("current_node_ids_json", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("version", Integer, nullable=False),
)

node_executions = Table(
    "node_executions",
    metadata,
    Column("id", String, primary_key=True),
    Column("execution_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("input_json", String, nullable=False),
    Column("output_json", String, nullable=True),
    Column("error_json", String, nullable=True),
    Column("idempotency_key", String, nullable=False),
    Column("started_at", String, nullable=True),
    Column("finished_at", String, nullable=True),
    Column("next_retry_at", String, nullable=True),
)

approval_requests = Table(
    "approval_requests",
    metadata,
    Column("approval_id", String, primary_key=True),
    Column("execution_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("node_execution_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("requested_at", String, nullable=False),
    Column("resolved_at", String, nullable=True),
    Column("resolved_by", String, nullable=True),
    Column("decision_payload_json", String, nullable=True),
    Column("version", Integer, nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("execution_id", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("event_type", String, nullable=False),
    Column("node_id", String, nullable=True),
    Column("payload_json", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("execution_id", "sequence", name="uq_events_execution_sequence"),
)

idempotency_records = Table(
    "idempotency_records",
    metadata,
    Column("idempotency_key", String, primary_key=True),
    Column("endpoint", String, primary_key=True),
    Column("response_json", String, nullable=False),
    Column("status_code", Integer, nullable=False),
    Column("created_at", String, nullable=False),
)
"""API-layer idempotency (Fase 10 §10.4): a repeated request with the same
`Idempotency-Key` header against the same endpoint replays the stored
response instead of re-running the operation. Keyed by (key, endpoint) so the
same key reused against a different endpoint doesn't collide. This is real
request-level deduplication — not to be confused with the node-level
`derive_idempotency_key` in `engine/retry.py`, which is a different concern
at a different layer.
"""
