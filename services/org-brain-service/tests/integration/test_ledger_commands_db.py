"""Ledger commands on real Postgres (issue 9): supersede under RLS, atomic
rollback with the outbox, and bitemporal reads across transactions.

Integration: recorded_at comes from the database clock per transaction, and
RLS/WITH CHECK only exist on Postgres.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from sqlalchemy import Engine, create_engine, func, select, text

from alembic import command
from app.config import OrgBrainConfig
from app.db import tenant_session
from app.models import Outbox, Status
from app.services import ledger
from tests.factories import TENANT_A, TENANT_B
from tests.services.test_ledger_commands import PERSON, draft

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def app() -> Iterator[Engine]:
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(OrgBrainConfig().app_database_url)
        yield engine
        engine.dispose()


def _db_now(engine: Engine) -> dt.datetime:
    with engine.connect() as conn:
        return conn.execute(text("SELECT clock_timestamp()")).scalar_one()


def test_supersede_creates_a_linked_record_and_two_outbox_rows(app: Engine) -> None:
    with tenant_session(TENANT_A, app) as s:
        old = ledger.append_new(
            s, tenant_id=TENANT_A, draft=draft(), status=Status.APPROVED, approved_by=PERSON
        )
    with tenant_session(TENANT_A, app) as s:
        before = s.scalar(select(func.count()).select_from(Outbox)) or 0
        new = ledger.supersede(
            s,
            tenant_id=TENANT_A,
            old_record_id=old.record_id,
            draft=draft(body={"text_ja": "改訂版"}),
            approved_by=PERSON,
        )
        after = s.scalar(select(func.count()).select_from(Outbox)) or 0
        old_now = ledger.latest(s, old.record_id)

    assert new.supersedes == old.record_id and new.record_id != old.record_id
    assert (old_now.version, old_now.status) == (2, "superseded")
    assert after - before == 2


def test_a_failed_command_leaves_neither_ledger_nor_outbox_rows(app: Engine) -> None:
    with tenant_session(TENANT_A, app) as s:
        records_before = s.execute(text("SELECT count(*) FROM memory_record")).scalar_one()
        outbox_before = s.execute(text("SELECT count(*) FROM outbox")).scalar_one()

    with pytest.raises(ledger.ModelCeilingError), tenant_session(TENANT_A, app) as s:
        ledger.append_new(s, tenant_id=TENANT_A, draft=draft())  # succeeds and flushes
        ledger.append_new(
            s, tenant_id=TENANT_A, draft=draft(asserted_by={"type": "model"}, authority=30)
        )

    with tenant_session(TENANT_A, app) as s:
        assert s.execute(text("SELECT count(*) FROM memory_record")).scalar_one() == records_before
        assert s.execute(text("SELECT count(*) FROM outbox")).scalar_one() == outbox_before


def test_another_tenant_cannot_transition_a_record(app: Engine) -> None:
    with tenant_session(TENANT_A, app) as s:
        row = ledger.append_new(s, tenant_id=TENANT_A, draft=draft())

    with pytest.raises(ledger.RecordNotFoundError), tenant_session(TENANT_B, app) as s:
        ledger.transition(s, record_id=row.record_id, to=Status.DEPRECATED)


def test_as_of_system_time_returns_the_version_held_then(app: Engine) -> None:
    with tenant_session(TENANT_A, app) as s:
        row = ledger.append_new(
            s, tenant_id=TENANT_A, draft=draft(), status=Status.APPROVED, approved_by=PERSON
        )
    between = _db_now(app)
    with tenant_session(TENANT_A, app) as s:
        ledger.transition(s, record_id=row.record_id, to=Status.DEPRECATED)

    with tenant_session(TENANT_A, app) as s:
        then = ledger.as_of(s, row.record_id, system_time=between)
        now = ledger.as_of(s, row.record_id, system_time=_db_now(app))
        before_it_existed = ledger.as_of(
            s, row.record_id, system_time=row.recorded_at - dt.timedelta(seconds=1)
        )

    assert then is not None and (then.version, then.status) == (1, "approved")
    assert now is not None and (now.version, now.status) == (2, "deprecated")
    assert before_it_existed is None


def test_as_of_business_time_respects_the_validity_window(app: Engine) -> None:
    window = draft(
        valid_from=dt.datetime(2026, 12, 1, tzinfo=dt.UTC),
        valid_to=dt.datetime(2027, 1, 1, tzinfo=dt.UTC),
    )
    with tenant_session(TENANT_A, app) as s:
        row = ledger.append_new(
            s, tenant_id=TENANT_A, draft=window, status=Status.APPROVED, approved_by=PERSON
        )
    now = _db_now(app)

    with tenant_session(TENANT_A, app) as s:
        inside = ledger.as_of(
            s,
            row.record_id,
            system_time=now,
            business_time=dt.datetime(2026, 12, 15, tzinfo=dt.UTC),
        )
        before = ledger.as_of(
            s,
            row.record_id,
            system_time=now,
            business_time=dt.datetime(2026, 11, 30, tzinfo=dt.UTC),
        )
        after = ledger.as_of(
            s, row.record_id, system_time=now, business_time=dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
        )

    assert inside is not None and inside.record_id == row.record_id
    assert before is None and after is None
