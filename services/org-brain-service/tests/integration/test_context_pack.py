"""Context packs end to end (issue 20): rules from the ledger, knowledge from
search revalidated against the ledger, stored and replayed. Real Postgres,
OPA and search projection; the fake "widgets" pack.

Every test uses its own widget code, so records from other tests never apply.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, Status
from app.projections import default_projection
from app.projections.base import SearchFilters, SearchQuery
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start
from app.workers import handlers  # noqa: F401 -- registers the projection handler
from app.workers.outbox import drain
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration

KEYS = {"askq2c-agent": "it-pack-agent-key", "other-agent": "it-pack-other-key"}


@dataclass
class World:
    api: TestClient
    seed: Seed

    @property
    def tenant(self) -> str:
        return self.seed.consumers["askq2c-agent"].tenant_id

    @property
    def team(self) -> str:
        return self.seed.node_ids["jp/finance/q2c-invoicing"]


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(cfg.seed_dir))
        register_tenants(seed)
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
        keys = ConsumerKeyStore(
            {c: hashlib.sha256(k.encode()).hexdigest() for c, k in KEYS.items()}
        )
        services = AppServices(seed=seed, consumer_keys=keys, policy_gate=gate)
        yield World(TestClient(create_app(services=services)), seed)
        app_engine().dispose()


def _record(
    w: World,
    *,
    kind: Kind,
    body: dict[str, Any],
    entities: tuple[str, ...] = (),
    scope: str | None = None,
    enforcement: str | None = None,
    sensitivity: str = "internal",
) -> str:
    with allow_network(), tenant_session(w.tenant) as s:
        row = ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": w.seed.people["OWN-001"]},
            draft=ledger.RecordDraft(
                kind=kind,
                schema_version=f"widgets.{kind.value}/1",
                body=body,
                scope=scope or w.team,
                asserted_by={"type": "import", "id": "it"},
                authority=20,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement=enforcement,
                entities=entities,
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": sensitivity},
            ),
        )
        return row.record_id


def _rule(
    w: World, code: str, *, enforcement: str = "mandatory", condition: str = "true", **kw: Any
) -> str:
    return _record(
        w,
        kind=Kind.RULE,
        enforcement=enforcement,
        entities=(f"widget:{code}",),
        body={
            "text_ja": f"{code} は検品必須。",
            "targets": ["order.label"],
            "condition": condition,
        },
        **kw,
    )


def _fact(w: World, code: str) -> str:
    return _record(w, kind=Kind.FACT, body={"text_ja": f"ウィジェット {code} は赤の在庫が少ない。"})


def _project(w: World) -> None:
    with allow_network():
        drain(app_engine(), w.tenant, limit=1000)


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _ask(w: World, code: str, key: str = "askq2c-agent", **overrides: Any) -> httpx.Response:
    body = {
        "domain": "widgets",
        "case": {
            "order_id": "O-1",
            "widget_code": code,
            "colour": "赤",
            "qty": 3,
            "order_date": "2026-11-02",
        },
        "scope": w.team,
        "purpose": "invoice_drafting",
        "as_of": "2026-11-02",
        **overrides,
    }
    with allow_network():
        return w.api.post(
            "/v1/cortex/context-packs", json=body, headers={"Authorization": f"Bearer {KEYS[key]}"}
        )


def _get(w: World, pack_id: str, key: str = "askq2c-agent") -> httpx.Response:
    with allow_network():
        return w.api.get(
            f"/v1/cortex/context-packs/{pack_id}", headers={"Authorization": f"Bearer {KEYS[key]}"}
        )


def _roles(pack: dict[str, Any]) -> dict[str, str]:
    return {r["record_id"]: r["role"] for r in pack["records"]}


def _served_count(w: World) -> int:
    with allow_network(), tenant_session(w.tenant) as s:
        return int(s.execute(text("SELECT count(*) FROM served_pack")).scalar_one())


def test_a_record_retired_in_the_ledger_but_still_projected_stays_out(world: World) -> None:
    code = _code()
    fact = _fact(world, code)
    _project(world)
    assert _roles(_ask(world, code).json()).get(fact) == "candidate"

    with allow_network(), tenant_session(world.tenant) as s:
        ledger.transition(s, record_id=fact, to=Status.DEPRECATED)  # outbox NOT drained
    with allow_network(), tenant_session(world.tenant) as s:
        hits = default_projection().search(
            s,
            SearchQuery(
                text=f"ウィジェット {code} 赤",
                filters=SearchFilters(kinds=("fact",), scopes=(world.team,)),
            ),
        )
        assert fact in {h.record_id for h in hits}  # the projection still says approved

    pack = _ask(world, code)

    assert pack.status_code == 201
    assert fact not in _roles(pack.json())


def test_the_pack_stores_the_policy_revision_and_record_versions(world: World) -> None:
    code = _code()
    rule, fact = _rule(world, code), _fact(world, code)
    _project(world)

    pack = _ask(world, code).json()

    with allow_network(), tenant_session(world.tenant) as s:
        served = s.execute(
            text(
                "SELECT policy_revision, trace_id, consumer_id, as_of FROM served_pack "
                "WHERE pack_id = :p"
            ),
            {"p": pack["pack_id"]},
        ).one()
        items = s.execute(
            text(
                "SELECT record_id::text, version, role FROM served_pack_item "
                "WHERE pack_id = :p ORDER BY position"
            ),
            {"p": pack["pack_id"]},
        ).all()
    assert served.policy_revision == pack["policy_revision"]
    assert served.policy_revision.startswith("sha256:")
    assert (served.consumer_id, served.as_of) == ("askq2c-agent", dt.date(2026, 11, 2))
    assert served.trace_id == pack["trace_id"]
    assert (rule, 1, "constraint") in [tuple(i) for i in items]
    assert (fact, 1, "candidate") in [tuple(i) for i in items]
    assert pack["records"][0]["role"] == "constraint"  # constraints first


def test_replay_returns_the_original_pack_after_the_rules_change(world: World) -> None:
    code = _code()
    rule = _rule(world, code)
    _fact(world, code)
    _project(world)
    served = _ask(world, code).json()

    with allow_network(), tenant_session(world.tenant) as s:
        ledger.transition(s, record_id=rule, to=Status.DEPRECATED)
    newcomer = _rule(world, code, enforcement="prohibition")
    _project(world)
    fresh = _ask(world, code).json()
    replay = _get(world, served["pack_id"])

    assert replay.status_code == 200
    assert json.dumps(replay.json(), sort_keys=True) == json.dumps(served, sort_keys=True)
    assert rule in _roles(replay.json()) and newcomer not in _roles(replay.json())
    assert rule not in _roles(fresh) and newcomer in _roles(fresh)


def test_another_tenants_key_cannot_read_the_pack(world: World) -> None:
    code = _code()
    _rule(world, code)
    pack = _ask(world, code).json()

    resp = _get(world, pack["pack_id"], key="other-agent")

    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "detail": "no such context pack"}
    assert _get(world, pack["pack_id"]).status_code == 200


def test_a_constraint_that_cannot_be_evaluated_holds_the_pack(world: World) -> None:
    code = _code()
    rule = _rule(world, code, condition="order.qty > 0")
    before = _served_count(world)

    resp = _ask(
        world,
        code,
        case={"order_id": "O-1", "widget_code": code, "colour": "赤", "order_date": "2026-11-02"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"] == "unevaluable_constraint"
    assert [d["record_id"] for d in resp.json()["detail"]] == [rule]
    assert _served_count(world) == before


def test_a_constraint_above_the_ceiling_holds_the_pack(world: World) -> None:
    code = _code()
    _rule(world, code, sensitivity="restricted")

    resp = _ask(world, code)

    assert resp.status_code == 409
    assert resp.json()["error"] == "constraint_above_ceiling"


def test_knowledge_above_the_ceiling_or_in_a_sibling_scope_is_dropped(world: World) -> None:
    code = _code()
    secret = _record(
        world,
        kind=Kind.FACT,
        body={"text_ja": f"ウィジェット {code} 赤 極秘"},
        sensitivity="restricted",
    )
    sibling = _record(
        world,
        kind=Kind.FACT,
        body={"text_ja": f"ウィジェット {code} 赤 入金"},
        scope=world.seed.node_ids["jp/finance/q2c-cash-application"],
    )
    _project(world)

    roles = _roles(_ask(world, code).json())

    assert secret not in roles and sibling not in roles


def test_unknown_domain_and_bad_case_are_422(world: World) -> None:
    assert _ask(world, _code(), domain="nope").json()["error"] == "unknown_domain"
    bad = _ask(world, _code(), case={"widget_code": "W-1"})
    assert (bad.status_code, bad.json()["error"]) == (422, "invalid_case")
