"""The PoC Postgres image: extensions and roles (issue 2).

Integration: needs the real image (pgvector, pg_bigm, role attributes), which
SQLite cannot emulate. Runs only through `make test-integration`.
"""

from collections.abc import Iterator

import psycopg2
import pytest
from psycopg2.extensions import connection as PgConnection

from app.config import OrgBrainConfig

pytestmark = pytest.mark.integration


def _connect(user: str, password: str) -> PgConnection:
    cfg = OrgBrainConfig()
    return psycopg2.connect(
        host=cfg.postgres_host,
        port=cfg.postgres_port,
        dbname=cfg.postgres_db,
        user=user,
        password=password,
        connect_timeout=5,
    )


@pytest.fixture
def owner() -> Iterator[PgConnection]:
    cfg = OrgBrainConfig()
    conn = _connect(cfg.org_brain_owner_user, cfg.org_brain_owner_password.get_secret_value())
    yield conn
    conn.close()


@pytest.fixture
def app_conn() -> Iterator[PgConnection]:
    cfg = OrgBrainConfig()
    conn = _connect(cfg.org_brain_app_user, cfg.org_brain_app_password.get_secret_value())
    yield conn
    conn.close()


def test_extensions_are_installed_at_pinned_versions(owner: PgConnection) -> None:
    with owner.cursor() as cur:
        cur.execute("SELECT extname, extversion FROM pg_extension ORDER BY extname")
        extensions = dict(cur.fetchall())

    assert extensions["vector"] == "0.8.7"
    assert extensions["pg_bigm"] == "1.2"


def test_server_is_postgres_16(owner: PgConnection) -> None:
    with owner.cursor() as cur:
        cur.execute("SHOW server_version_num")
        (version_num,) = cur.fetchone() or ("0",)

    assert 160012 <= int(version_num) < 170000


def test_pg_bigm_splits_japanese_and_indexes_it(owner: PgConnection) -> None:
    with owner.cursor() as cur:
        cur.execute("SELECT show_bigm('架空電機株式会社')")
        (bigrams,) = cur.fetchone() or ([],)
        cur.execute("CREATE TEMP TABLE bigm_probe (body text)")
        cur.execute("CREATE INDEX bigm_probe_idx ON bigm_probe USING gin (body gin_bigm_ops)")
        cur.execute(
            "INSERT INTO bigm_probe VALUES ('注文番号を備考に記載'), ('振込手数料を差し引く')"
        )
        cur.execute("SELECT body FROM bigm_probe WHERE body LIKE likequery('備考')")
        hits = [row[0] for row in cur.fetchall()]
    owner.rollback()

    assert "電機" in bigrams
    assert "株式" in bigrams
    assert hits == ["注文番号を備考に記載"]


def test_pgvector_distance_works(owner: PgConnection) -> None:
    with owner.cursor() as cur:
        cur.execute("SELECT '[1,0,0]'::vector <-> '[0,1,0]'::vector")
        (distance,) = cur.fetchone() or (0.0,)

    assert distance == pytest.approx(2**0.5)


def test_app_role_cannot_bypass_rls_or_own_anything(owner: PgConnection) -> None:
    with owner.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname = 'org_brain_app'"
        )
        attrs = cur.fetchone()

    assert attrs == (False, False, False, False)


def test_owner_owns_the_database_and_schema(owner: PgConnection) -> None:
    with owner.cursor() as cur:
        cur.execute(
            "SELECT pg_get_userbyid(d.datdba), pg_get_userbyid(n.nspowner) "
            "FROM pg_database d, pg_namespace n "
            "WHERE d.datname = current_database() AND n.nspname = 'public'"
        )
        owners = cur.fetchone()

    assert owners == ("org_brain_owner", "org_brain_owner")


def test_app_role_cannot_create_tables(app_conn: PgConnection) -> None:
    with app_conn.cursor() as cur, pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute("CREATE TABLE public.not_allowed (id int)")
