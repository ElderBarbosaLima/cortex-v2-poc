"""Outbox handlers. Importing this module registers them (app.workers.outbox.HANDLERS)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.projections.postgres_search import PostgresSearchProjection
from app.services.ledger import RECORD_APPENDED
from app.workers.outbox import OutboxOutcome, register

search_projection = PostgresSearchProjection()


@register(RECORD_APPENDED)
def project_record(session: Session, payload: dict[str, Any]) -> OutboxOutcome | None:
    """Keep the search projection in step with the ledger (at-least-once safe:
    the upsert never moves a document to an older version)."""
    search_projection.apply(session, payload)
    return None
