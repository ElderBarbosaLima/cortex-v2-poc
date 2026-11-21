"""Outbox registry and drainer (shape of project-orchestrator's outbox_registry
and drainer, RAY-81898).

An outbox row carries only an `event_type` and a JSON `payload`; this registry
is the one place that maps an event type to the handler that acts on it.
Delivery is at-least-once, so a handler must be safe to run twice for the same
row.

The outbox is under row-level security, so it is drained one tenant at a time
(`drain`), iterating the `tenant` registry (`drain_all`); no role bypasses RLS.
Each row runs in a SAVEPOINT: a handler that raises rolls back only its own
writes, the row records the error and attempt, and the remaining rows go on.
After `max_attempts` failures a row is `failed` (the dead-letter state, like a
stream's `:failed`).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db import tenant_session
from app.models import Outbox, Tenant

_logger = structlog.get_logger("org-brain.outbox")


class OutboxOutcome(Enum):
    DISPATCHED = "dispatched"
    SKIPPED = "skipped"


Handler = Callable[[Session, dict[str, Any]], "OutboxOutcome | None"]
HANDLERS: dict[str, Handler] = {}


class UnregisteredEventTypeError(LookupError):
    """Loud on purpose: a missing registration means a class of work never happens."""


def register(event_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if event_type in HANDLERS:
            raise ValueError(f"{event_type} already has a handler")
        HANDLERS[event_type] = fn
        return fn

    return deco


@dataclass
class DrainResult:
    dispatched: int = 0
    skipped: int = 0
    retried: int = 0
    failed: int = 0


def drain(
    engine: Engine,
    tenant_id: str,
    *,
    handlers: dict[str, Handler] | None = None,
    limit: int = 100,
    max_attempts: int = 3,
) -> DrainResult:
    registry = HANDLERS if handlers is None else handlers
    result = DrainResult()
    with tenant_session(tenant_id, engine) as session:
        rows = session.scalars(
            select(Outbox)
            .where(Outbox.status == "pending")
            .order_by(Outbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.claimed_at = dt.datetime.now(dt.UTC)
            try:
                with session.begin_nested():
                    handler = registry.get(row.event_type)
                    if handler is None:
                        raise UnregisteredEventTypeError(row.event_type)
                    outcome = handler(session, row.payload)
            except Exception as exc:
                row.attempt_count += 1
                row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                row.claimed_at = None
                if row.attempt_count >= max_attempts:
                    row.status, result.failed = "failed", result.failed + 1
                else:
                    result.retried += 1
                _logger.warning(
                    "outbox.handler_failed",
                    outbox_id=row.id,
                    event_type=row.event_type,
                    attempt=row.attempt_count,
                    error=row.last_error,
                )
                continue
            row.dispatched_at = dt.datetime.now(dt.UTC)
            if outcome is OutboxOutcome.SKIPPED:
                row.status, result.skipped = "skipped", result.skipped + 1
            else:
                row.status, result.dispatched = "dispatched", result.dispatched + 1
    return result


def tenant_ids(engine: Engine) -> list[str]:
    with Session(engine) as session:
        return [str(t) for t in session.scalars(select(Tenant.tenant_id).order_by(Tenant.key))]


def drain_all(engine: Engine, **kwargs: Any) -> dict[str, DrainResult]:
    return {tenant: drain(engine, tenant, **kwargs) for tenant in tenant_ids(engine)}
