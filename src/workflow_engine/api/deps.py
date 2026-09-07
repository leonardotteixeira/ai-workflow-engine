"""Dependency providers — Fase 10 §10.5.

Nothing here is a module-level global: the SQLAlchemy `Engine`, the
`LLMProvider`, and the `NodeExecutorRegistry` are all built once in
`app.py`'s lifespan and stored on `app.state`, then handed out per-request
through FastAPI's `Depends`. This is what makes it possible to spin up a
fully isolated app (its own `:memory:` database, its own Mock provider) per
test — see `tests/api/conftest.py` — without any shared state leaking
between tests.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Connection, Engine

from workflow_engine.engine.node_executor import NodeExecutorRegistry


def get_db_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.db_engine
    return engine


def get_node_registry(request: Request) -> NodeExecutorRegistry:
    registry: NodeExecutorRegistry = request.app.state.node_registry
    return registry


def get_connection(request: Request) -> Iterator[Connection]:
    """A single connection per request for *read* endpoints (list/get) that
    don't need `PersistentExecutionEngine`'s write transaction — still one
    `db.begin()` so a read-only endpoint can't accidentally hold a
    long-lived, uncommitted transaction across a slow client.
    """
    engine: Engine = request.app.state.db_engine
    with engine.begin() as conn:
        yield conn
