"""Owner alerts and the April renewal (issue 48, slide 18): rules that need a
look (review due, expiring, often overridden, keeps losing), and renewing a
rule's end date through the review queue. Real OPA + Postgres; fake pack."""

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
AGENT, DESK = "it-alert-agent", "it-alert-desk"


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
        yield World(
            TestClient(
                create_app(
                    services=AppServices(
                        seed=seed,
                        consumer_keys=ConsumerKeyStore(
                            {
                                "askq2c-agent": hashlib.sha256(AGENT.encode()).hexdigest(),
                                "decision-desk": hashlib.sha256(DESK.encode()).hexdigest(),
                            }
                        ),
                        policy_gate=gate,
                    )
                )
            ),
            seed,
        )
        app_engine().dispose()


def _call(
    w: World,
    method: str,
    path: str,
    body: Any = None,
    *,
    key: str = AGENT,
    user: str | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"}
    if user:
        headers["X-End-User-Id"] = w.seed.people[user]
    with allow_network():
        return w.api.request(method, path, json=body, headers=headers, params=params)


def _rule(
    w: World,
    code: str,
    *,
    scope: str = "jp/finance/q2c-invoicing",
    enforcement: str = "default",
    valid_to: dt.date | None = None,
    text_ja: str = "ラベルに型番を入れる。",
) -> str:
    with allow_network(), tenant_session(w.tenant) as s:
        return ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": w.seed.people["OWN-001"]},
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="widgets.rule/1",
                body={
                    "text_ja": text_ja,
                    "targets": ["order.label"],
                    "condition": f'order.widget_code == "{code}"',
                },
                scope=w.seed.node_ids[scope],
                asserted_by={"type": "import", "id": "it"},
                authority=20,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                valid_to=business_start(valid_to) if valid_to else None,
                enforcement=enforcement,
                entities=(f"widget:{code}",),
                sensitivity={"class": "internal"},
            ),
        ).record_id


def _case(code: str, order: str) -> dict[str, Any]:
    return {
        "order_id": order,
        "widget_code": code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-05",
    }


def _pack(w: World, code: str, order: str) -> dict[str, Any]:
    resp = _call(
        w,
        "POST",
        "/v1/cortex/context-packs",
        {
            "domain": "widgets",
            "case": _case(code, order),
            "scope": w.team,
            "purpose": "invoice_drafting",
            "as_of": "2026-11-05",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _alerts(w: World) -> dict[str, set[str]]:
    resp = _call(
        w,
        "GET",
        "/v1/cortex/rules/alerts",
        key=DESK,
        user="OWN-001",
        params={"scope": w.team, "purpose": "rule_review", "within_days": 30},
    )
    assert resp.status_code == 200, resp.text
    return {i["record_id"]: {a["kind"] for a in i["alerts"]} for i in resp.json()["items"]}


def test_each_kind_of_alert(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def code() -> str:
        return f"W-{uuid.uuid4().hex[:8]}"  # the fake pack recognises exactly this shape

    soon = dt.date.today() + dt.timedelta(days=10)
    c_exp, c_cor, c_los = code(), code(), code()
    expiring = _rule(world, c_exp, valid_to=soon)
    corrected = _rule(world, c_cor)
    loser = _rule(world, c_los)
    _rule(world, c_los, scope="jp", enforcement="prohibition", text_ja="独自の文言を付けない。")
    for n in range(3):
        _pack(world, c_los, f"L-{n}")  # the local default loses every time
        order = f"C-{c_cor}-{n}"
        pack = _pack(world, c_cor, order)
        resp = _call(
            world,
            "POST",
            "/v1/cortex/corrections",
            user="R-001",
            body={
                "pack_id": pack["pack_id"],
                "domain": "widgets",
                "case_id": order,
                "case": _case(c_cor, order),
                "correction": {
                    "case_id": order,
                    "target": "order.label",
                    "after": "別ラベル",
                    "reviewer": "R-001",
                },
                "idempotency_key": f"alert-{c_cor}-{n}",
            },
        )
        assert resp.status_code == 202, resp.text

    alerts = _alerts(world)
    monkeypatch.setenv("REVIEW_EVERY_DAYS", "0")
    due = _alerts(world)

    assert alerts[expiring] == {"expiring"}
    assert alerts[corrected] == {"often_overridden"}
    assert alerts[loser] == {"keeps_losing"}
    assert "review_due" in due[expiring]  # with a zero-day review cycle every rule is due


def test_april_renewal_goes_through_the_queue(world: World) -> None:
    code = f"W-{uuid.uuid4().hex[:8]}"
    old = _rule(world, code, valid_to=dt.date(2027, 3, 31))

    renewed = _call(
        world,
        "POST",
        "/v1/cortex/rules/renew",
        user="R-001",
        body={"record_ids": [old], "valid_to": "2028-03-31", "purpose": "invoice_drafting"},
    ).json()
    again = _call(
        world,
        "POST",
        "/v1/cortex/rules/renew",
        user="R-001",
        body={"record_ids": [old], "valid_to": "2028-03-31", "purpose": "invoice_drafting"},
    ).json()
    item = renewed["items"][0]
    decided = _call(
        world,
        "POST",
        f"/v1/cortex/review-cases/{item['review_case_id']}/approve",
        key=DESK,
        user="OWN-001",
        body={"record_id": item["proposed_record_id"], "authority": 20},
    )

    assert item["status"] == "proposed"
    assert again["items"][0]["status"] == "pending"  # asked twice, queued once
    assert decided.status_code == 200, decided.text
    with allow_network(), tenant_session(world.tenant) as s:
        assert ledger.latest(s, old).status == "superseded"
        new = ledger.latest(s, item["proposed_record_id"])
    assert (new.status, new.supersedes) == ("approved", old)
    assert new.valid_to == business_start(dt.date(2028, 3, 31))
