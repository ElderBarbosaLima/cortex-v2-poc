"""ORM-level append-only guard on memory_record (hermetic: in-memory SQLite).

The database trigger is the real guarantee (tests/integration/test_ledger_db.py);
this proves the named error fires on both SQLAlchemy paths.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, LedgerImmutableError, MemoryRecord
from tests.factories import memory_record


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(memory_record())
        s.commit()
        yield s


def test_inserting_versions_is_allowed(session: Session) -> None:
    first = session.scalars(select(MemoryRecord)).one()
    session.add(memory_record(record_id=first.record_id, version=2, status="deprecated"))
    session.commit()

    assert len(session.scalars(select(MemoryRecord)).all()) == 2


def test_changing_a_loaded_row_is_rejected(session: Session) -> None:
    row = session.scalars(select(MemoryRecord)).one()
    row.status = "deprecated"

    with pytest.raises(LedgerImmutableError):
        session.flush()


def test_deleting_a_loaded_row_is_rejected(session: Session) -> None:
    session.delete(session.scalars(select(MemoryRecord)).one())

    with pytest.raises(LedgerImmutableError):
        session.flush()


def test_bulk_update_is_rejected(session: Session) -> None:
    with pytest.raises(LedgerImmutableError):
        session.execute(update(MemoryRecord).values(status="deprecated"))


def test_bulk_delete_is_rejected(session: Session) -> None:
    with pytest.raises(LedgerImmutableError):
        session.execute(delete(MemoryRecord))
