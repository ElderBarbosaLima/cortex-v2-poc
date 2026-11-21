"""Append-only enforcement at the ORM layer (shape of the monorepo's
org-brain-service `app/models/immutable.py`).

The real guarantee is the BEFORE UPDATE OR DELETE trigger from migration 001,
plus the app role having no UPDATE/DELETE grant. This listener gives a named
error instead of a database exception, and makes the property assertable in
hermetic SQLite tests. Two paths must be blocked because SQLAlchemy routes them
through different events: unit-of-work flushes (mapper events) and bulk
`update()`/`delete()` statements (`do_orm_execute`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Mapper, ORMExecuteState, Session

GUARDED_MODELS: set[type[Any]] = set()


class LedgerImmutableError(RuntimeError):
    """Raised when code attempts to change or remove an append-only row."""


def _reject_bulk(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete):
        return
    for mapper in state.all_mappers:
        if mapper.class_ in GUARDED_MODELS:
            verb = "updated" if state.is_update else "deleted"
            raise LedgerImmutableError(
                f"{mapper.class_.__name__} is append-only; a bulk statement cannot leave rows "
                f"{verb} — append a new version instead"
            )


def _reject_unit_of_work(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    raise LedgerImmutableError(
        f"{type(target).__name__} is append-only; append a new version instead"
    )


def enforce_append_only(model: type[Any]) -> None:
    if model in GUARDED_MODELS:
        return
    GUARDED_MODELS.add(model)
    event.listen(model, "before_update", _reject_unit_of_work)
    event.listen(model, "before_delete", _reject_unit_of_work)


# One session-wide listener for bulk statements; it only acts on GUARDED_MODELS.
event.listen(Session, "do_orm_execute", _reject_bulk)
