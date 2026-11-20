"""SQLite savepoint and database isolation utilities for hermetic tests.

Implements session-scoped in-memory SQLite schema reuse with per-test savepoint
rollbacks, eliminating redundant table creation/destruction overhead across test
suites while keeping tests 100% isolated.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Generator, Sequence
from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _sqlite_jsonb_exists(json_str: str | None, key: str) -> bool:
    """Emulate Postgres jsonb_exists(col, key) in SQLite."""
    if not json_str:
        return False
    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            return key in data
        if isinstance(data, list):
            return key in data
    except (ValueError, TypeError):
        pass
    return False


def register_sqlite_emulations(dbapi_connection: Any) -> None:
    """Register custom SQLite functions emulating Postgres-specific primitives."""
    create_fn = getattr(dbapi_connection, "create_function", None)
    if callable(create_fn):
        create_fn("jsonb_exists", 2, _sqlite_jsonb_exists)


def create_savepoint_engine(
    metadata: MetaData,
    *,
    seeds_fn: Callable[[Session], None] | None = None,
    extra_ddl_fn: Callable[[Connection], None] | None = None,
) -> Engine:
    """Create a session-scoped in-memory SQLite engine configured for savepoint rollback.

    Configures pysqlite to disable automatic transaction wrapping so nested SAVEPOINTs
    coordinate cleanly with the outer transaction fixture.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        dbapi_connection.isolation_level = None
        register_sqlite_emulations(dbapi_connection)

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN")

    metadata.create_all(engine)

    if extra_ddl_fn is not None:
        with engine.begin() as conn:
            extra_ddl_fn(conn)

    if seeds_fn is not None:
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with SessionLocal() as session:
            seeds_fn(session)
            session.commit()

    return engine


def create_fresh_engine(
    metadata: MetaData,
    *,
    seeds_fn: Callable[[Session], None] | None = None,
    extra_ddl_fn: Callable[[Connection], None] | None = None,
) -> Engine:
    """Create a standalone, unshared in-memory SQLite engine for a single test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        register_sqlite_emulations(dbapi_connection)

    metadata.create_all(engine)

    if extra_ddl_fn is not None:
        with engine.begin() as conn:
            extra_ddl_fn(conn)

    if seeds_fn is not None:
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        with SessionLocal() as session:
            seeds_fn(session)
            session.commit()

    return engine


@contextlib.contextmanager
def savepoint_session(engine: Engine) -> Generator[Session, None, None]:
    """Yield a Session joined to an outer transaction via SAVEPOINT, rolling back on exit."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
    )
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def is_fresh_db_requested(
    request: Any,
    service_env_var: str | None = None,
    fallback_modules: Sequence[str] = (),
) -> bool:
    """Check if the current test requests a fresh database engine.

    Returns True if:
    - The test is marked with @pytest.mark.fresh_db.
    - An environment variable like <SVC>_TEST_FRESH_DB is set to "1".
    - The module name matches any in fallback_modules (e.g. tests using after_commit hooks).
    """
    if request is not None:
        node = getattr(request, "node", None)
        if node is not None:
            marker = node.get_closest_marker("fresh_db")
            if marker is not None:
                return True

        module = getattr(request, "module", None)
        if module is not None and fallback_modules:
            mod_name = getattr(module, "__name__", "").rsplit(".", 1)[-1]
            if mod_name in fallback_modules:
                return True

    return bool(service_env_var and os.environ.get(service_env_var) == "1")
