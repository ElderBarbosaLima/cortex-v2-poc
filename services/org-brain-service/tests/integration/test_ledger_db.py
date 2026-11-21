"""Ledger on real Postgres: append-only trigger, row-level security, grants (issue 8).

Integration: RLS, triggers and role privileges do not exist in SQLite.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from alembic import command
from app.config import OrgBrainConfig
from app.db import tenant_session
from tests.factories import TENANT_A, TENANT_B, memory_record

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engines() -> Iterator[tuple[Engine, Engine]]:
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        owner = create_engine(cfg.owner_database_url)
        app = create_engine(cfg.app_database_url)
        yield owner, app
        owner.dispose()
        app.dispose()


@pytest.fixture(scope="module")
def seeded(engines: tuple[Engine, Engine]) -> dict[str, str]:
    _, app = engines
    a, b = memory_record(TENANT_A), memory_record(TENANT_B)
    with allow_network():
        with tenant_session(TENANT_A, app) as s:
            s.add(a)
            s.add(memory_record(TENANT_A, record_id=a.record_id, version=2, status="deprecated"))
        with tenant_session(TENANT_B, app) as s:
            s.add(b)
    return {"a": a.record_id, "b": b.record_id}


def _count(engine: Engine, table: str, tenant: str | None) -> int:
    with engine.begin() as conn:
        if tenant is not None:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_without_a_tenant_every_table_reads_empty(
    engines: tuple[Engine, Engine], seeded: dict[str, str]
) -> None:
    owner, app = engines

    assert _count(app, "memory_record", None) == 0
    assert _count(app, "outbox", None) == 0
    assert _count(owner, "memory_record", None) == 0  # FORCE ROW LEVEL SECURITY binds the owner too


def test_a_tenant_reads_only_its_own_rows_even_with_raw_sql(
    engines: tuple[Engine, Engine], seeded: dict[str, str]
) -> None:
    _, app = engines

    with app.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        rows = conn.execute(text("SELECT DISTINCT tenant_id::text FROM memory_record")).all()
        sneak = conn.execute(
            text("SELECT count(*) FROM memory_record WHERE tenant_id = :b"), {"b": TENANT_B}
        ).scalar_one()

    assert rows == [(TENANT_A,)]
    assert sneak == 0


def test_writing_into_another_tenant_is_refused(engines: tuple[Engine, Engine]) -> None:
    _, app = engines

    with pytest.raises(ProgrammingError, match="row-level security"):  # noqa: SIM117
        with tenant_session(TENANT_A, app) as s:
            s.add(memory_record(TENANT_B))


def test_current_view_shows_the_latest_version_under_rls(
    engines: tuple[Engine, Engine], seeded: dict[str, str]
) -> None:
    _, app = engines

    with app.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        current = conn.execute(
            text("SELECT version, status FROM memory_record_current WHERE record_id = :r"),
            {"r": seeded["a"]},
        ).all()
        other = conn.execute(
            text("SELECT count(*) FROM memory_record_current WHERE record_id = :r"),
            {"r": seeded["b"]},
        ).scalar_one()

    assert current == [(2, "deprecated")]
    assert other == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE memory_record SET status = 'erased'",
        "DELETE FROM memory_record",
        "TRUNCATE memory_record",
    ],
)
def test_the_owner_cannot_change_history(
    engines: tuple[Engine, Engine], seeded: dict[str, str], statement: str
) -> None:
    owner, _ = engines

    with pytest.raises(DBAPIError, match="append-only"), owner.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        conn.execute(text(statement))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE memory_record SET status = 'erased'", "DELETE FROM memory_record"],
)
def test_the_app_role_has_no_update_or_delete_grant(
    engines: tuple[Engine, Engine], seeded: dict[str, str], statement: str
) -> None:
    _, app = engines

    with pytest.raises(ProgrammingError, match="permission denied"), app.begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
        conn.execute(text(statement))


def test_constraints_reject_an_out_of_vocabulary_record(engines: tuple[Engine, Engine]) -> None:
    _, app = engines

    with pytest.raises(DBAPIError, match="memory_record_authority_check"):  # noqa: SIM117
        with tenant_session(TENANT_A, app) as s:
            s.add(memory_record(TENANT_A, authority=50))
    with pytest.raises(DBAPIError, match="ck_rule_has_enforcement"):  # noqa: SIM117
        with tenant_session(TENANT_A, app) as s:
            s.add(memory_record(TENANT_A, enforcement=None))
