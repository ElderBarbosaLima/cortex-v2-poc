"""Outbox handlers. Importing this module registers them (app.workers.outbox.HANDLERS)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.projections.postgres_search import PostgresSearchProjection
from app.services.evidence import EXPORT_REQUESTED, run_export
from app.services.ledger import RECORD_APPENDED
from app.workers.outbox import OutboxOutcome, register

search_projection = PostgresSearchProjection()


@register(RECORD_APPENDED)
def project_record(session: Session, payload: dict[str, Any]) -> OutboxOutcome | None:
    """Keep the search projection in step with the ledger (at-least-once safe:
    the upsert never moves a document to an older version)."""
    search_projection.apply(session, payload)
    return None


@register(EXPORT_REQUESTED)
def export_evidence(session: Session, payload: dict[str, Any]) -> OutboxOutcome | None:
    """Archive a served pack's manifest (issue 23). Safe to run twice: an
    exported row is skipped, and an object already in the bucket with the same
    manifest hash is reused, never written again under retention."""
    return None if run_export(session, str(payload["export_id"])) else OutboxOutcome.SKIPPED
