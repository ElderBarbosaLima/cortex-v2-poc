"""Database engine and tenant-bound sessions.

The service connects as `org_brain_app`, which owns no table and cannot bypass
row-level security. Every unit of work runs inside `tenant_session()`, which
sets `app.tenant_id` for that transaction only (`SET LOCAL` via set_config(...,
true)); without it every RLS-protected table reads as empty.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.config import OrgBrainConfig


@functools.cache
def app_engine() -> Engine:
    return create_engine(OrgBrainConfig().app_database_url, pool_pre_ping=True)


@contextmanager
def tenant_session(tenant_id: str, engine: Engine | None = None) -> Generator[Session]:
    """One transaction bound to one tenant. Commits on success, rolls back on error.

    Lazy: no connection is taken until the session first needs one, and the
    tenant is set at that moment (`after_begin`). A request rejected before it
    touches the database (validation, auth) never opens a connection."""
    tenant = str(uuid.UUID(tenant_id))  # reject anything that is not a UUID
    factory = sessionmaker(bind=engine or app_engine(), expire_on_commit=False)
    with factory() as session:

        @event.listens_for(session, "after_begin")
        def _bind_tenant(
            sess: Session, transaction: SessionTransaction, connection: Connection
        ) -> None:
            connection.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})

        with session.begin():
            yield session
