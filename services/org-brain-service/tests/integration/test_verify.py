"""Verify end to end (issue 21): the release check reads the ledger, never the
projection. Real Postgres + OPA; the fake "widgets" pack. Each test mints its
own widget code, so records from other tests never apply.
"""

from __future__ import annotations

import datetime as dt
import hashlib
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
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, Status
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration

KEYS = {"askq2c-agent": "it-verify-agent-key", "other-agent": "it-verify-other-key"}


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


def _rule(w: World, code: str, *, enforcement: str = "mandatory", label: str | None = None) -> str:
    body: dict[str, Any] = {
        "text_ja": f"{code} は検品必須。",
        "targets": ["order.label"],
        "condition": f'order.widget_code == "{code}"',
    }
    if label:
        body["label"] = label
    with allow_network(), tenant_session(w.tenant) as s:
        return ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": w.seed.people["OWN-001"]},
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="widgets.rule/1",
                body=body,
                scope=w.team,
                asserted_by={"type": "import", "id": "it"},
                authority=20,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement=enforcement,
                entities=(f"widget:{code}",),
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": "internal"},
            ),
        ).record_id


def _retire(w: World, record_id: str) -> None:
    with allow_network(), tenant_session(w.tenant) as s:
        ledger.transition(s, record_id=record_id, to=Status.DEPRECATED)  # outbox not drained


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _case(code: str) -> dict[str, Any]:
    return {
        "order_id": "O-1",
        "widget_code": code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-02",
    }


def _post(w: World, path: str, body: dict[str, Any], key: str = "askq2c-agent") -> httpx.Response:
    with allow_network():
        return w.api.post(path, json=body, headers={"Authorization": f"Bearer {KEYS[key]}"})


def _pack(w: World, code: str) -> str:
    resp = _post(
        w,
        "/v1/cortex/context-packs",
        {
            "domain": "widgets",
            "case": _case(code),
            "scope": w.team,
            "purpose": "invoice_drafting",
            "as_of": "2026-11-02",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["pack_id"]


def _verify(
    w: World, pack_id: str, code: str, output: dict[str, Any] | None = None, **kw: Any
) -> httpx.Response:
    body = {"pack_id": pack_id, "case": _case(code), "output": output or {}, **kw}
    return _post(w, "/v1/cortex/verify", body, key=kw.pop("key", "askq2c-agent"))


def _reasons(resp: httpx.Response) -> list[tuple[str, str | None]]:
    return [(r["reason"], r["record_id"]) for r in resp.json()["reasons"]]


def test_a_rule_retired_after_the_pack_holds_even_with_a_stale_projection(world: World) -> None:
    code = _code()
    rule = _rule(world, code)
    pack = _pack(world, code)
    _retire(world, rule)

    resp = _verify(world, pack, code)

    assert resp.status_code == 200
    assert resp.json()["result"] == "hold"
    assert _reasons(resp) == [("rule_no_longer_approved", rule)]


def test_a_new_mandatory_rule_after_the_pack_holds(world: World) -> None:
    code = _code()
    _rule(world, code)
    pack = _pack(world, code)
    newcomer = _rule(world, code, enforcement="prohibition")

    resp = _verify(world, pack, code)

    assert resp.json()["result"] == "hold"
    assert _reasons(resp) == [("new_mandatory_rule", newcomer)]


def test_nothing_changed_passes(world: World) -> None:
    code = _code()
    _rule(world, code)
    pack = _pack(world, code)

    resp = _verify(world, pack, code)

    assert (resp.status_code, resp.json()["result"], resp.json()["reasons"]) == (200, "pass", [])
    assert resp.json()["policy_revision"].startswith("sha256:")


def test_a_retired_default_does_not_hold(world: World) -> None:
    code = _code()
    default = _rule(world, code, enforcement="default")
    pack = _pack(world, code)
    _retire(world, default)

    assert _verify(world, pack, code).json()["result"] == "pass"


def test_a_missing_or_other_tenants_pack_is_404(world: World) -> None:
    code = _code()
    pack = _pack(world, code)

    missing = _verify(world, str(uuid.uuid4()), code)
    foreign = _post(
        world,
        "/v1/cortex/verify",
        {"pack_id": pack, "case": _case(code), "output": {}},
        key="other-agent",
    )

    assert missing.status_code == foreign.status_code == 404
    assert foreign.json() == {"error": "not_found", "detail": "no such context pack"}


def test_the_pack_checks_the_output(world: World) -> None:
    code = _code()
    _rule(world, code, label="取扱注意")
    pack = _pack(world, code)

    wrong = _verify(world, pack, code, output={"label": "通常"})
    right = _verify(world, pack, code, output={"label": "取扱注意"})

    assert wrong.json()["result"] == "hold"
    assert wrong.json()["reasons"] == [
        {"reason": "output_check_failed", "record_id": None, "detail": "label must be '取扱注意'"}
    ]
    assert right.json()["result"] == "pass"


def test_another_case_than_the_packs_is_422(world: World) -> None:
    code = _code()
    pack = _pack(world, code)

    resp = _post(
        world,
        "/v1/cortex/verify",
        {"pack_id": pack, "case": {**_case(code), "qty": 99}, "output": {}},
    )

    assert (resp.status_code, resp.json()["error"]) == (422, "case_mismatch")


def test_every_result_is_stored_append_only_and_linked_to_the_pack(world: World) -> None:
    code = _code()
    rule = _rule(world, code)
    pack = _pack(world, code)
    passed = _verify(world, pack, code).json()
    _retire(world, rule)
    held = _verify(world, pack, code).json()

    with allow_network(), tenant_session(world.tenant) as s:
        rows = s.execute(
            text(
                "SELECT verification_id::text, result, reasons, output_hash, policy_revision "
                "FROM verification WHERE pack_id = :p ORDER BY id"
            ),
            {"p": pack},
        ).all()
    assert [(r.verification_id, r.result) for r in rows] == [
        (passed["verification_id"], "pass"),
        (held["verification_id"], "hold"),
    ]
    assert rows[1].reasons[0]["reason"] == "rule_no_longer_approved"
    assert rows[0].output_hash == hashlib.sha256(b"{}").hexdigest()
    update = text("UPDATE verification SET result = 'pass' WHERE pack_id = :p")
    # Two layers: the app role has no UPDATE grant, and even the table owner
    # (who bypasses grants) is stopped by the append-only trigger.
    with (
        allow_network(),
        pytest.raises(DBAPIError, match="permission denied"),
        app_engine().begin() as conn,
    ):
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world.tenant})
        conn.execute(update, {"p": pack})
    owner = create_engine(OrgBrainConfig().owner_database_url)
    try:
        with (
            allow_network(),
            pytest.raises(DBAPIError, match="append-only"),
            owner.begin() as conn,  # FORCE RLS binds the owner too: name the tenant
        ):
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world.tenant})
            conn.execute(update, {"p": pack})
    finally:
        owner.dispose()
