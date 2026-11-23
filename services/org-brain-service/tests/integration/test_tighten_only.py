"""Scopes only tighten (issue 31): a lower-scope default that would loosen an
inherited constraint is refused for machines and flagged for people at
intake; read-time precedence is unchanged. Real OPA + Postgres; fake pack."""

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
from app.services import ledger, review
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration
KEY = "it-tighten-key"


@dataclass
class World:
    api: TestClient
    seed: Seed

    @property
    def tenant(self) -> str:
        return self.seed.consumers["askq2c-agent"].tenant_id


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
        keys = ConsumerKeyStore({"askq2c-agent": hashlib.sha256(KEY.encode()).hexdigest()})
        yield World(
            TestClient(
                create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
            ),
            seed,
        )
        app_engine().dispose()


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _constraint(
    w: World,
    code: str,
    *,
    scope: str = "jp",
    target: str = "order.label",
    enforcement: str = "prohibition",
    entity: str | None = None,
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
                    "text_ja": "ラベルに独自の文言を加えない。",
                    "targets": [target],
                    "condition": "true",
                },
                scope=w.seed.node_ids[scope],
                asserted_by={"type": "import", "id": "it"},
                authority=30,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement=enforcement,
                entities=(entity or f"widget:{code}",),
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": "internal"},
            ),
        ).record_id


def _propose(
    w: World,
    code: str,
    *,
    enforcement: str = "default",
    user: str | None = "R-001",
    target: str = "order.label",
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {KEY}"}
    if user:
        headers["X-End-User-Id"] = w.seed.people[user]
    body: dict[str, Any] = {
        "domain": "widgets",
        "kind": "rule",
        "schema_version": "widgets.rule/1",
        "body": {
            "text_ja": f"{code} には「急ぎ」ラベルを付ける。",
            "targets": [target],
            "condition": f'order.widget_code == "{code}"',
            "label": "急ぎ",
        },
        "scope": w.seed.node_ids["jp/finance/q2c-invoicing"],
        "entities": [f"widget:{code}"],
        "enforcement": enforcement,
        "authority": 10,
        "sensitivity": {"class": "internal"},
        "purpose": "invoice_drafting",
        "valid_from": "2026-11-01",
    }
    with allow_network():
        return w.api.post("/v1/cortex/records", json=body, headers=headers)


def test_a_person_may_propose_a_loosening_default_but_the_owner_is_told(world: World) -> None:
    code = _code()
    ban = _constraint(world, code)

    resp = _propose(world, code)

    assert resp.status_code == 201, resp.text
    assert [w["code"] for w in resp.json()["warnings"]] == ["loosens_inherited_constraint"]
    assert ban in resp.json()["warnings"][0]["detail"]
    with allow_network(), tenant_session(world.tenant) as s:
        row = ledger.latest(s, resp.json()["record_id"])
        shown = review.inherited_conflicts(s, row)
    assert row.source["inherited_conflicts"][0]["code"] == "loosens_inherited_constraint"
    assert row.source["inherited_conflicts"][0]["record_id"] == ban
    assert "text_ja" not in str(row.source)  # the constraint's text is never copied in clear
    # issue 38: the Decision Desk gets the rule itself, opened when the case is read
    assert shown == [
        {
            "record_id": ban,
            "enforcement": "prohibition",
            "text_ja": "ラベルに独自の文言を加えない。",
            "targets": ["order.label"],
        }
    ]


def test_the_same_proposal_twice_is_one_proposal(world: World) -> None:
    """Issue 39: proposing a rule that already waits for a decision returns that one."""
    code = _code()
    _constraint(world, code)

    first, again = _propose(world, code).json(), _propose(world, code).json()

    assert first["already_pending"] is False
    assert again["already_pending"] is True
    assert (again["record_id"], again["review_case_id"]) == (
        first["record_id"],
        first["review_case_id"],
    )
    assert [w["code"] for w in again["warnings"]] == ["loosens_inherited_constraint"]


def test_a_machine_may_not_loosen_an_inherited_constraint(world: World) -> None:
    code = _code()
    _constraint(world, code, enforcement="mandatory")

    resp = _propose(world, code, user=None)  # the agent alone: asserted as a model

    assert resp.status_code == 422
    assert [p["code"] for p in resp.json()["detail"]] == ["loosens_inherited_constraint"]


@pytest.mark.parametrize(
    ("case", "kwargs"),
    [
        ("a stricter local rule tightens", {"enforcement": "mandatory"}),
        ("another target does not compete", {"target": "order.colour_check"}),
    ],
)
def test_what_tightens_or_does_not_overlap_passes(
    world: World, case: str, kwargs: dict[str, Any]
) -> None:
    code = _code()
    _constraint(world, code)

    resp = _propose(world, code, user=None, **kwargs)

    assert resp.status_code == 201, (case, resp.text)
    assert resp.json()["warnings"] == []


def test_a_constraint_about_another_entity_or_a_sibling_scope_does_not_count(world: World) -> None:
    code = _code()
    _constraint(world, code, entity=f"widget:{_code()}")
    _constraint(world, code, scope="jp/finance/q2c-cash-application")

    resp = _propose(world, code, user=None)

    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == []


def test_a_suggested_rule_that_would_loosen_is_not_proposed(world: World) -> None:
    code = _code()
    _constraint(world, code)
    case = {
        "order_id": "O-1",
        "widget_code": code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-02",
    }
    headers = {"Authorization": f"Bearer {KEY}"}
    with allow_network():
        pack = world.api.post(
            "/v1/cortex/context-packs",
            headers=headers,
            json={
                "domain": "widgets",
                "case": case,
                "scope": world.seed.node_ids["jp/finance/q2c-invoicing"],
                "purpose": "invoice_drafting",
                "as_of": "2026-11-02",
            },
        ).json()
        resp = world.api.post(
            "/v1/cortex/corrections",
            headers={**headers, "X-End-User-Id": world.seed.people["R-001"]},
            json={
                "pack_id": pack["pack_id"],
                "domain": "widgets",
                "case_id": "O-1",
                "case": case,
                "correction": {
                    "case_id": "O-1",
                    "target": "order.label",
                    "after": "急ぎ",
                    "reviewer": "R-001",
                },
                "idempotency_key": f"idem-{uuid.uuid4().hex}",
            },
        )

    assert resp.status_code == 202, resp.text
    assert resp.json()["proposed_record_id"] is None
    assert "loosens_inherited_constraint" in [p["code"] for p in resp.json()["proposal_problems"]]
