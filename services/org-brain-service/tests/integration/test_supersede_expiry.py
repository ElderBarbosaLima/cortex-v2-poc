"""Supersede and expiry through the review queue (issue 33). Real OPA +
Postgres; the fake "widgets" pack; each test mints its own widget code."""

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
AGENT, DESK = "it-sup-agent", "it-sup-desk"


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

    def person(self, key: str) -> str:
        return self.seed.people[key]


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
            {
                "askq2c-agent": hashlib.sha256(AGENT.encode()).hexdigest(),
                "decision-desk": hashlib.sha256(DESK.encode()).hexdigest(),
            }
        )
        yield World(
            TestClient(
                create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
            ),
            seed,
        )
        app_engine().dispose()


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _case(code: str, date: str = "2026-11-05") -> dict[str, Any]:
    return {"order_id": "O-1", "widget_code": code, "colour": "赤", "qty": 3, "order_date": date}


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
        headers["X-End-User-Id"] = w.person(user)
    with allow_network():
        return w.api.request(method, path, json=body, headers=headers, params=params)


def _approved_rule(w: World, code: str) -> str:
    with allow_network(), tenant_session(w.tenant) as s:
        return ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": w.person("OWN-001")},
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="widgets.rule/1",
                body={
                    "text_ja": f"{code} は検品必須（旧）。",
                    "targets": ["order.label"],
                    "condition": f'order.widget_code == "{code}"',
                },
                scope=w.team,
                asserted_by={"type": "import", "id": "it"},
                authority=30,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement="mandatory",
                entities=(f"widget:{code}",),
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": "internal"},
            ),
        ).record_id


def _propose(w: World, code: str, **extra: Any) -> httpx.Response:
    return _call(
        w,
        "POST",
        "/v1/cortex/records",
        user="R-001",
        body={
            "domain": "widgets",
            "kind": "rule",
            "schema_version": "widgets.rule/1",
            "body": {
                "text_ja": f"{code} は検品必須、写真も添付する。",
                "targets": ["order.label"],
                "condition": f'order.widget_code == "{code}"',
            },
            "scope": w.team,
            "entities": [f"widget:{code}"],
            "enforcement": "mandatory",
            "authority": 10,
            "sensitivity": {"class": "internal"},
            "purpose": "invoice_drafting",
            "valid_from": "2026-11-01",
            **extra,
        },
    )


def _approve(w: World, case_id: str, **extra: Any) -> httpx.Response:
    return _call(
        w,
        "POST",
        f"/v1/cortex/review-cases/{case_id}/approve",
        key=DESK,
        user="OWN-001",
        body={"authority": 30, "enforcement": "mandatory", **extra},
    )


def _pack(w: World, code: str, date: str = "2026-11-05") -> dict[str, Any]:
    resp = _call(
        w,
        "POST",
        "/v1/cortex/context-packs",
        body={
            "domain": "widgets",
            "case": _case(code, date),
            "scope": w.team,
            "purpose": "invoice_drafting",
            "as_of": date,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rules(pack: dict[str, Any]) -> set[str]:
    return {r["record_id"] for r in pack["records"] if r["kind"] == "rule"}


def _verify(w: World, pack: dict[str, Any], code: str, date: str = "2026-11-05") -> dict[str, Any]:
    return _call(
        w,
        "POST",
        "/v1/cortex/verify",
        body={"pack_id": pack["pack_id"], "case": _case(code, date), "output": {}},
    ).json()


def test_a_supersede_through_the_review_queue_holds_old_packs_and_serves_the_new_rule(
    world: World,
) -> None:
    code = _code()
    old = _approved_rule(world, code)
    before = _pack(world, code)
    assert old in _rules(before)

    proposed = _propose(world, code, supersedes=old)
    assert proposed.status_code == 201, proposed.text
    new = proposed.json()["record_id"]
    decided = _approve(world, proposed.json()["review_case_id"])

    assert decided.status_code == 200, decided.text
    assert {(r["record_id"], r["status"]) for r in decided.json()["records"]} == {
        (new, "approved"),
        (old, "superseded"),
    }
    held = _verify(world, before, code)
    assert held["result"] == "hold"
    assert ("rule_no_longer_approved", old) in {
        (r["reason"], r["record_id"]) for r in held["reasons"]
    }
    after = _pack(world, code)
    assert new in _rules(after) and old not in _rules(after)
    detail = _call(
        world,
        "GET",
        f"/v1/cortex/review-cases/{proposed.json()['review_case_id']}",
        key=DESK,
        user="OWN-001",
        params={"purpose": "rule_review"},
    ).json()
    assert detail["proposals"][0]["supersedes"] == old  # lineage on the case


def test_only_an_approved_record_of_the_same_kind_can_be_superseded(world: World) -> None:
    code = _code()
    pending = _propose(world, code).json()["record_id"]

    unknown = _propose(world, code, supersedes=str(uuid.uuid4()))
    not_approved = _propose(world, code, supersedes=pending)

    assert [p["code"] for p in unknown.json()["detail"]] == ["unknown_superseded"]
    assert [p["code"] for p in not_approved.json()["detail"]] == ["not_supersedable"]


def test_the_owner_sets_validity_and_packs_follow_the_business_date(world: World) -> None:
    code = _code()
    proposed = _propose(world, code).json()
    rid = proposed["record_id"]

    bad = _approve(
        world, proposed["review_case_id"], valid_from="2026-11-10", valid_to="2026-11-01"
    )
    ok = _approve(world, proposed["review_case_id"], valid_from="2026-11-01", valid_to="2026-11-10")

    assert (bad.status_code, bad.json()["error"]) == (422, "invalid_decision")
    assert ok.status_code == 200, ok.text
    inside, outside = _pack(world, code, "2026-11-05"), _pack(world, code, "2026-11-20")
    assert rid in _rules(inside) and rid not in _rules(outside)
    # Business time: an event of 5 Nov is judged by the rules of 5 Nov, so its
    # pack still verifies after the rule's end date.
    assert _verify(world, inside, code)["result"] == "pass"

    expiring = _call(
        world,
        "GET",
        "/v1/cortex/records/expiring",
        key=DESK,
        user="OWN-001",
        params={"scope": world.team, "purpose": "rule_review", "within_days": 30},
    ).json()
    row = next(r for r in expiring["items"] if r["record_id"] == rid)
    assert row["expired"] is True
    assert row["valid_to"].startswith("2026-11-09T15:00")  # 10 Nov, 00:00 JST


def test_a_rule_ending_soon_is_listed_before_it_expires(world: World) -> None:
    code = _code()
    soon = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=5)).isoformat()
    proposed = _propose(world, code).json()
    assert _approve(world, proposed["review_case_id"], valid_to=soon).status_code == 200

    def listed(days: int) -> dict[str, Any] | None:
        page = _call(
            world,
            "GET",
            "/v1/cortex/records/expiring",
            key=DESK,
            user="OWN-001",
            params={"scope": world.team, "purpose": "rule_review", "within_days": days},
        ).json()
        return next((r for r in page["items"] if r["record_id"] == proposed["record_id"]), None)

    assert listed(2) is None
    row = listed(10)
    assert row is not None and row["expired"] is False
    assert row["title"]  # issue 38: the Desk lists it by its text, not an id
