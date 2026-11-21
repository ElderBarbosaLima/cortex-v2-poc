"""Search projection on real Postgres (issue 10): pg_bigm for Japanese, rebuild
gives identical results (D12 criterion a), the outbox drainer's failure path,
tenant isolation, and a projection that lags the ledger.

Integration: pg_bigm, tsvector, pgvector and RLS only exist on Postgres.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from sqlalchemy import Engine, create_engine, select, text

from alembic import command
from app.config import OrgBrainConfig
from app.db import tenant_session
from app.models import Kind, Outbox, Status, Tenant
from app.projections.base import Candidate, SearchFilters, SearchQuery
from app.services import ledger
from app.workers.handlers import search_projection
from app.workers.outbox import HANDLERS, drain

pytestmark = pytest.mark.integration

SYNTHETIC = Path("/data/synthetic")
TENANT_JP = "5e4c0000-0000-4000-8000-0000000000a1"
TENANT_OTHER = "5e4c0000-0000-4000-8000-0000000000b2"
TENANT_VEC = "5e4c0000-0000-4000-8000-0000000000c3"
PERSON = {"approver_type": "user", "user": "OWN-001"}

QUERIES = [
    "備考",
    "振込手数料",
    "登録番号",
    "消費税",
    "差押",
    "部門コード",
    "前受金",
    "雑損失",
    "検収書",
    "注文番号",
    "請求書",
    "入金",
    "記載",
    "Coupa",
    "Ariba",
    "D-77",
    "customer:CUST-0042",
    "invoice.registration_number",
    "支払条件 月末締め",
    "与信",
]


def _rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for workflow in ("custom_invoices", "cash_application"):
        d = SYNTHETIC / workflow
        rules += json.loads((d / "rules.json").read_text())
        learned = json.loads((d / "corrections.jsonl").read_text().splitlines()[0])["expected_rule"]
        rules.append(learned)
    return rules


@pytest.fixture(scope="module")
def stack() -> Iterator[tuple[Engine, dict[str, str]]]:
    """Every synthetic rule (both workflows, plus the learned ones) in the ledger,
    projected by draining the outbox. Returns the app engine and record_id -> key."""
    nodes = {
        n["key"]: n["obj_id"] for n in json.loads((SYNTHETIC / "tenant.json").read_text())["nodes"]
    }
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        engine = create_engine(OrgBrainConfig().app_database_url)
        keys: dict[str, str] = {}
        with tenant_session(TENANT_JP, engine) as s:
            s.add_all(
                [
                    Tenant(tenant_id=TENANT_JP, key="jp-search"),
                    Tenant(tenant_id=TENANT_OTHER, key="other-search"),
                ]
            )
        for rule in _rules():
            tenant = TENANT_JP if rule["tenant"] == "jp" else TENANT_OTHER
            with tenant_session(tenant, engine) as s:
                row = ledger.append_new(
                    s,
                    tenant_id=tenant,
                    draft=ledger.RecordDraft(
                        kind=Kind.RULE,
                        schema_version=rule["schema_version"],
                        body=rule["body"],
                        scope=nodes[rule["scope"]],
                        asserted_by={"type": "import", "id": "test"},
                        authority=rule["authority"] if rule["authority"] <= 10 else 10,
                        source=rule["source"],
                        valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
                        enforcement=rule["enforcement"],
                        entities=tuple(rule["entities"]),
                    ),
                    status=Status.APPROVED,
                    approved_by=PERSON,
                )
                keys[row.record_id] = rule["key"]
        drain(engine, TENANT_JP)
        drain(engine, TENANT_OTHER)
        yield engine, keys
        engine.dispose()


def _search(engine: Engine, tenant: str, q: str, **filters: Any) -> list[Candidate]:
    with tenant_session(tenant, engine) as s:
        return search_projection.search(s, SearchQuery(text=q, filters=SearchFilters(**filters)))


def test_japanese_substrings_are_found_through_pg_bigm(
    stack: tuple[Engine, dict[str, str]],
) -> None:
    engine, keys = stack

    remarks = {keys[c.record_id] for c in _search(engine, TENANT_JP, "備考")}
    fee = [keys[c.record_id] for c in _search(engine, TENANT_JP, "振込手数料")]

    assert {"CI-C-0063-PAYTERMS", "CI-L-0042-COUPA"} <= remarks
    assert not {k for k in remarks if k.startswith("CA-")}
    assert fee[0] == "CA-L-0042-FEE"
    with tenant_session(TENANT_JP, engine) as s:
        plan = "\n".join(
            r[0]
            for r in s.execute(
                text("EXPLAIN SELECT 1 FROM search_doc WHERE search_text LIKE likequery('備考')")
            )
        )
    assert "search_doc" in plan


def test_rebuilding_the_projection_gives_identical_results(
    stack: tuple[Engine, dict[str, str]],
) -> None:
    engine, _ = stack
    before = {q: _search(engine, TENANT_JP, q) for q in QUERIES}
    assert sum(1 for hits in before.values() if hits) >= 18  # the queries really hit something

    with tenant_session(TENANT_JP, engine) as s:
        s.execute(text("DELETE FROM search_doc"))
    assert _search(engine, TENANT_JP, "備考") == []
    with tenant_session(TENANT_JP, engine) as s:
        rebuilt = search_projection.rebuild(s, TENANT_JP)
    after = {q: _search(engine, TENANT_JP, q) for q in QUERIES}

    jp_records = sum(1 for key in stack[1].values() if not key.startswith("OT-"))
    assert rebuilt == jp_records  # this tenant only; the other tenant's rules stay out
    assert after == before


def test_search_never_crosses_tenants(stack: tuple[Engine, dict[str, str]]) -> None:
    engine, keys = stack

    other = [keys[c.record_id] for c in _search(engine, TENANT_OTHER, "差押")]
    jp = {keys[c.record_id] for c in _search(engine, TENANT_JP, "別テナント")}

    assert other == ["OT-CA-GARNISH"]
    assert not {k for k in jp if k.startswith("OT-")}


def test_a_lagging_projection_still_shows_a_retired_record_until_drained(
    stack: tuple[Engine, dict[str, str]],
) -> None:
    engine, keys = stack
    dept = next(rid for rid, key in keys.items() if key == "CI-C-0017-DEPT")

    with tenant_session(TENANT_JP, engine) as s:
        ledger.transition(s, record_id=dept, to=Status.DEPRECATED)
    stale = [c.record_id for c in _search(engine, TENANT_JP, "部門コード")]
    drain(engine, TENANT_JP)
    fresh = [c.record_id for c in _search(engine, TENANT_JP, "部門コード")]

    assert dept in stale  # the access gate must revalidate (issue 20)
    assert dept not in fresh


def test_a_failing_handler_is_retried_then_failed_while_other_rows_go_on(
    stack: tuple[Engine, dict[str, str]],
) -> None:
    engine, _ = stack

    def boom(session: object, payload: dict[str, Any]) -> None:
        raise RuntimeError("projection target unavailable")

    with tenant_session(TENANT_JP, engine) as s:
        s.add(Outbox(tenant_id=TENANT_JP, event_type="test.boom", payload={"n": 1}))
        ledger.append_new(
            s,
            tenant_id=TENANT_JP,
            draft=ledger.RecordDraft(
                kind=Kind.FACT,
                schema_version="test.fact/1",
                body={"text_ja": "テスト事実"},
                scope=TENANT_JP,
                asserted_by={"type": "user", "id": "t"},
                authority=0,
                source={"system": "test"},
                valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            ),
        )
    handlers = {**HANDLERS, "test.boom": boom}

    first = drain(engine, TENANT_JP, handlers=handlers, max_attempts=2)
    second = drain(engine, TENANT_JP, handlers=handlers, max_attempts=2)
    with tenant_session(TENANT_JP, engine) as s:
        row = s.scalars(select(Outbox).where(Outbox.event_type == "test.boom")).one()

    assert (first.dispatched, first.retried, first.failed) == (1, 1, 0)
    assert (second.dispatched, second.retried, second.failed) == (0, 0, 1)
    assert (row.status, row.attempt_count) == ("failed", 2)
    assert row.last_error == "RuntimeError: projection target unavailable"
    assert [
        c.record_id
        for c in _search(engine, TENANT_JP, "テスト事実", kinds=("fact",), statuses=("proposed",))
    ]


def test_fixed_test_vectors_rank_the_closer_document_first(
    stack: tuple[Engine, dict[str, str]],
) -> None:
    engine, _ = stack
    close = [1.0, 0, 0, 0, 0, 0, 0, 0]
    far = [0, 1.0, 0, 0, 0, 0, 0, 0]
    ids: dict[str, str] = {}
    with tenant_session(TENANT_VEC, engine) as s:
        s.add(Tenant(tenant_id=TENANT_VEC, key="vec-search"))
    for name, vec in (("close", close), ("far", far)):
        with tenant_session(TENANT_VEC, engine) as s:
            row = ledger.append_new(
                s,
                tenant_id=TENANT_VEC,
                draft=ledger.RecordDraft(
                    kind=Kind.CONTENT,
                    schema_version="test.content/1",
                    body={"text_ja": "ベクトル検証", "test_vector": vec},
                    scope=TENANT_VEC,
                    asserted_by={"type": "user", "id": "t"},
                    authority=0,
                    source={"system": "test"},
                    valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
                ),
                status=Status.APPROVED,
                approved_by=PERSON,
            )
            ids[row.record_id] = name
    drain(engine, TENANT_VEC)

    with tenant_session(TENANT_VEC, engine) as s:
        hits = search_projection.search(
            s,
            SearchQuery(
                text="ベクトル検証", vector=tuple(close), filters=SearchFilters(kinds=("content",))
            ),
        )

    assert [ids[h.record_id] for h in hits] == ["close", "far"]
