"""SQLite engine construction — DESIGN.md §10.

WAL mode (`PRAGMA journal_mode=WAL`) is set on every new DBAPI connection, not
once globally: SQLite's WAL setting is per-connection-pool but the pragma
itself must be issued on each physical connection for the driver's pooling to
apply it consistently. `PRAGMA foreign_keys=ON` is also set here since
SQLite disables FK enforcement by default even when the schema declares them.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

from workflow_engine.persistence.schema import metadata


def create_sqlite_engine(path: str) -> Engine:
    """`path` is a filesystem path, or ":memory:" for an in-memory database.

    `check_same_thread=False` + (for `:memory:` only) `StaticPool` are both
    needed once a request can be served from a worker thread pool (FastAPI's
    sync dependencies/endpoints run this way): SQLite's default forbids
    reusing a connection across threads, and a `:memory:` database only
    exists for the lifetime of the single connection that created it — with
    the default pool, a connection checked out from a different thread would
    silently get its own *empty* database. `StaticPool` pins every checkout
    to the one physical connection so a `:memory:` engine behaves like a
    single shared database regardless of which thread touches it (this is
    also what makes each test's own `:memory:` engine fully isolated from
    every other test's — a fresh `create_sqlite_engine(":memory:")` call is a
    fresh, empty database every time).
    """
    connect_args: dict[str, object] = {"check_same_thread": False}
    kwargs: dict[str, object] = {"connect_args": connect_args, "future": True}
    if path == ":memory:":
        kwargs["poolclass"] = StaticPool
    engine = create_engine(f"sqlite:///{path}", **kwargs)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    metadata.create_all(engine)
