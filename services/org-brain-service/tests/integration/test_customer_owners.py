"""Owners per customer and customer groups (issue 45, slide 18): a rule about a
customer is decided by that customer's cash specialist (owner or backup), not
by the scope's owner; the Desk names them; a group's rule reaches its
subsidiaries' payments."""

from __future__ import annotations

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
from app.db import app_engine
from app.domain_packs.base import register_pack
from app.domain_packs.cash_allocation import PACK
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.seed.tenants import Seed, load_seed
from app.services.policy_gate import PolicyGate

pytestmark = pytest.mark.integration
AGENT, DESK = "it-own-agent", "it-own-desk"


@dataclass
class World:
    api: TestClient
    seed: Seed


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
    w: World, method: str, path: str, body: Any = None, *, key: str = AGENT, user: str | None = None
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"}
    if user:
        headers["X-End-User-Id"] = w.seed.people[user]
    with allow_network():
        return w.api.request(
            method,
            path,
            json=body,
            headers=headers,
            params={"purpose": "rule_review"} if key == DESK and method == "GET" else None,
        )


def _propose(w: World, customer: str) -> dict[str, Any]:
    resp = _call(
        w,
        "POST",
        "/v1/cortex/records",
        user="R-101",
        body={
            "domain": "cash_allocation",
            "kind": "rule",
            "schema_version": "cash_allocation.rule/1",
            "body": {
                "text_ja": f"{customer} は手数料 330 円を差し引く。{uuid.uuid4().hex[:6]}",
                "targets": ["allocation.bank_fee"],
                # gap 7: no other test's payment matches, so approving it changes no pack
                "condition": f'payment.customer_id == "{customer}" && payment.gap == 7',
                "exception": "bank_fee",
                "engine": {"expected_fee": 330},
            },
            "scope": w.seed.node_ids["jp/finance/q2c-cash-application"],
            "entities": [f"customer:{customer}"],
            "enforcement": "default",
            "authority": 10,
            "sensitivity": {"class": "internal"},
            "purpose": "cash_matching",
            "valid_from": "2026-11-01",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _approve(w: World, proposed: dict[str, Any], user: str) -> httpx.Response:
    return _call(
        w,
        "POST",
        f"/v1/cortex/review-cases/{proposed['review_case_id']}/approve",
        key=DESK,
        user=user,
        body={"record_id": proposed["record_id"], "authority": 20},
    )


def test_the_customers_cash_specialist_decides_not_the_scopes_owner(world: World) -> None:
    proposed = _propose(world, "CUST-0101")

    detail = _call(
        world,
        "GET",
        f"/v1/cortex/review-cases/{proposed['review_case_id']}",
        key=DESK,
        user="CS-101",
    ).json()
    by_owner = _approve(world, proposed, "OWN-001")
    by_specialist = _approve(world, proposed, "CS-101")

    assert detail["named_owners"] == {
        "owner": world.seed.people["CS-101"],
        "backup": world.seed.people["CS-102"],
    }
    assert detail["can_decide"]["approve"]["allowed"] is True
    assert by_owner.status_code == 403
    assert by_owner.json()["detail"] == "only the named owner or backup of this customer can decide"
    assert by_specialist.status_code == 200, by_specialist.text


def test_a_customer_with_no_owner_of_its_own_is_decided_by_the_scopes_owner(world: World) -> None:
    proposed = _propose(world, "CUST-0105")

    assert _approve(world, proposed, "CS-101").status_code == 403
    assert _approve(world, proposed, "OWN-001").status_code == 200
