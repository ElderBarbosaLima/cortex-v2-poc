"""IBM's three operations (issues 43–44, slide 20), as `ibm-pr-agent`:
1. get approved rules for the rule engine, synced by cursor;
2. get context for a payment, every rule citing its version and approver;
3. report the outcome.
Everything else stays 403, including corrections and verify."""

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

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.domain_packs.cash_allocation import PACK
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, Status
from app.seed.rules import load_pack_rules
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start

pytestmark = pytest.mark.integration
IBM = "it-ibm-key"


@dataclass
class World:
    api: TestClient
    seed: Seed
    case: dict[str, Any]

    @property
    def scope(self) -> str:
        return self.seed.node_ids["jp/finance/q2c-cash-application"]

    @property
    def tenant(self) -> str:
        return self.seed.consumers["ibm-pr-agent"].tenant_id


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(cfg.seed_dir))
        register_tenants(seed)
        load_pack_rules(seed, Path(cfg.seed_dir))
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
        api = TestClient(
            create_app(
                services=AppServices(
                    seed=seed,
                    consumer_keys=ConsumerKeyStore(
                        {"ibm-pr-agent": hashlib.sha256(IBM.encode()).hexdigest()}
                    ),
                    policy_gate=gate,
                )
            )
        )
        cases = [
            json.loads(x)
            for x in (Path(cfg.seed_dir) / "cash_allocation" / "cases.jsonl")
            .read_text()
            .splitlines()
        ]
        yield World(api, seed, cases[0])
        app_engine().dispose()


def _call(
    w: World,
    method: str,
    path: str,
    *,
    body: Any = None,
    params: dict[str, Any] | None = None,
    user: str | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {IBM}"}
    if user:
        headers["X-End-User-Id"] = w.seed.people[user]
    with allow_network():
        return w.api.request(method, path, json=body, params=params, headers=headers)


def _sync(w: World, cursor: str | None = None) -> dict[str, Any]:
    resp = _call(
        w,
        "GET",
        "/v1/cortex/rules/sync",
        params={
            "domain": "cash_allocation",
            "scope": w.scope,
            "purpose": "payment_reconciliation",
            "cursor": cursor,
            "limit": 1000,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rule(w: World, code: str) -> str:
    with allow_network(), tenant_session(w.tenant) as s:
        return ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": w.seed.people["OWN-001"]},
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="cash_allocation.rule/1",
                body={
                    "text_ja": f"{code} は分割で支払う。",
                    "targets": ["allocation.partial_payment"],
                    "condition": f'payment.customer_id == "{code}" && payment.gap > 0',
                    "exception": "partial_payment",
                    "engine": {"action": "leave_balance_open"},
                },
                scope=w.scope,
                asserted_by={"type": "user", "id": w.seed.people["R-101"]},
                authority=20,
                source={},
                valid_from=business_start(dt.date(2026, 11, 1)),
                enforcement="default",
                entities=(f"customer:{code}",),
                sensitivity={"class": "internal", "personal_data": False},
            ),
        ).record_id


def test_the_rule_engine_syncs_everything_then_only_what_changed(world: World) -> None:
    first = _sync(world)
    seeded = [
        i
        for i in first["items"]
        if i["body"] and i["body"].get("text_ja", "").startswith("山田商事")
    ]
    code = f"CUST-{uuid.uuid4().hex[:6]}"
    added = _rule(world, code)

    after_add = _sync(world, first["next_cursor"])
    with allow_network(), tenant_session(world.tenant) as s:
        ledger.transition(s, record_id=added, to=Status.DEPRECATED)  # retired by its owner
    after_retire = _sync(world, after_add["next_cursor"])
    quiet = _sync(world, after_retire["next_cursor"])

    assert {i["change"] for i in first["items"]} == {"upsert"}  # nothing removed yet at the start
    assert seeded and seeded[0]["body"]["engine"] == {
        "expected_fee": 660,
        "max_fee": 880,
        "treatment": "fee_expense",
    }
    assert seeded[0]["approved_by"] == world.seed.people["OWN-001"]
    assert [(i["record_id"], i["change"]) for i in after_add["items"]] == [(added, "upsert")]
    assert [(i["record_id"], i["change"], i["body"]) for i in after_retire["items"]] == [
        (added, "remove", None)
    ]
    assert quiet["items"] == [] and quiet["next_cursor"] == after_retire["next_cursor"]
    assert all(
        i["body"] is None or "engine" in i["body"] or "exception" not in i["body"]
        for i in first["items"]
    )


def test_context_cites_each_rule_with_version_and_approver_and_the_outcome_is_remembered(
    world: World,
) -> None:
    case = {**world.case, "case_id": f"PAY-{uuid.uuid4().hex[:6]}"}
    pack = _call(
        world,
        "POST",
        "/v1/cortex/context-packs",
        body={
            "domain": "cash_allocation",
            "case": case,
            "scope": world.scope,
            "purpose": "payment_reconciliation",
            "as_of": case["payment"]["value_date"],
        },
    ).json()
    rule = next(r for r in pack["records"] if r["kind"] == "rule")

    reported = _call(
        world,
        "POST",
        "/v1/cortex/outcomes",
        user="R-101",
        body={
            "pack_id": pack["pack_id"],
            "domain": "cash_allocation",
            "case_id": case["case_id"],
            "case": case,
            "correction": {
                "correction_id": "O-ibm-1",
                "case_id": case["case_id"],
                "reviewer": "R-101",
                "corrected_at": "2026-12-04T15:00:00+09:00",
                "result": "confirmed",
                "exception": "bank_fee",
                "selected_invoices": ["INV-A100"],
                "note_ja": "手数料。",
            },
            "idempotency_key": f"ibm-{uuid.uuid4().hex}",
        },
    )
    again = _call(
        world,
        "POST",
        "/v1/cortex/context-packs",
        body={
            "domain": "cash_allocation",
            "case": {**case, "case_id": "PAY-next"},
            "scope": world.scope,
            "purpose": "payment_reconciliation",
            "as_of": case["payment"]["value_date"],
        },
    ).json()

    assert (rule["version"], rule["approved_by"]) == (1, world.seed.people["OWN-001"])
    assert reported.status_code == 202, reported.text
    assert reported.json()["proposed_record_id"] is None
    assert reported.json()["decision_record_id"] in {
        r["record_id"] for r in again["records"] if r["role"] == "similar_case"
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/cortex/corrections"),
        ("POST", "/v1/cortex/verify"),
        ("POST", "/v1/cortex/records"),
        ("GET", "/v1/cortex/review-cases"),
    ],
)
def test_everything_else_is_still_out_of_the_profile(world: World, method: str, path: str) -> None:
    resp = _call(
        world,
        method,
        path,
        body={} if method == "POST" else None,
        params={"scope": world.scope, "purpose": "payment_reconciliation"},
    )
    assert (resp.status_code, resp.json()["error"]) == (403, "insufficient_scope")


def test_a_cursor_is_opaque_and_a_made_up_one_is_refused(world: World) -> None:
    cursor = _sync(world)["next_cursor"]
    resp = _call(
        world,
        "GET",
        "/v1/cortex/rules/sync",
        params={
            "domain": "cash_allocation",
            "scope": world.scope,
            "purpose": "payment_reconciliation",
            "cursor": "12345",
        },
    )

    assert not cursor.isdigit()  # no internal row number leaves the service
    assert (resp.status_code, resp.json()["error"]) == (422, "invalid_cursor")
