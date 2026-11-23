"""Past cases as memory (issue 42). Every reviewed outcome, confirmed or
corrected, is kept as a decision with the pack's summary; the next pack for
the same customer carries the closest ones as `similar_case`. A confirmed
outcome proposes no rule. Stored with the pack: replayed as served, shredded
by erasure. Never another customer's."""

from __future__ import annotations

import copy
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
from app.keys import install as install_keys
from app.main import AppServices, create_app, register_tenants
from app.seed.rules import load_pack_rules
from app.seed.tenants import Seed, load_seed
from app.services import erasure
from app.services.body_codec import PlainJsonCodec, install_codec
from app.services.policy_gate import PolicyGate

pytestmark = pytest.mark.integration
AGENT = "it-past-agent"


@dataclass
class World:
    api: TestClient
    seed: Seed
    base: dict[str, Any]

    @property
    def scope(self) -> str:
        return self.seed.node_ids["jp/finance/q2c-cash-application"]

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
        load_pack_rules(seed, Path(cfg.seed_dir))
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
        install_keys(t.tenant_id for t in seed.tenants)  # sealed bodies, so erasure can shred
        api = TestClient(
            create_app(
                services=AppServices(
                    seed=seed,
                    consumer_keys=ConsumerKeyStore(
                        {"askq2c-agent": hashlib.sha256(AGENT.encode()).hexdigest()}
                    ),
                    policy_gate=gate,
                )
            )
        )
        cases = [
            json.loads(line)
            for line in (Path(cfg.seed_dir) / "cash_allocation" / "cases.jsonl")
            .read_text()
            .splitlines()
        ]
        yield World(api, seed, next(c for c in cases if c["case_id"] == "PAY-A001"))
        install_codec(PlainJsonCodec(), default=True)  # the other modules expect plain bodies
        app_engine().dispose()


def _case(w: World, customer: str, gap: int) -> dict[str, Any]:
    case = copy.deepcopy(w.base)
    pid = f"PAY-{uuid.uuid4().hex[:8]}"
    case |= {"case_id": pid, "customer_id": customer, "gap": gap}
    case["payment"]["payment_id"] = pid
    return case


def _call(
    w: World, path: str, body: Any, user: str | None = None, method: str = "POST"
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {AGENT}"}
    if user:
        headers["X-End-User-Id"] = w.seed.people[user]
    with allow_network():
        return w.api.request(method, path, json=body, headers=headers)


def _pack(w: World, case: dict[str, Any]) -> dict[str, Any]:
    resp = _call(
        w,
        "/v1/cortex/context-packs",
        {
            "domain": "cash_allocation",
            "case": case,
            "scope": w.scope,
            "purpose": "cash_matching",
            "as_of": case["payment"]["value_date"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _report(w: World, case: dict[str, Any], **outcome: Any) -> dict[str, Any]:
    pack = _pack(w, case)
    resp = _call(
        w,
        "/v1/cortex/corrections",
        {
            "pack_id": pack["pack_id"],
            "domain": "cash_allocation",
            "case_id": case["case_id"],
            "case": case,
            "correction": {
                "correction_id": f"O-{uuid.uuid4().hex[:6]}",
                "case_id": case["case_id"],
                "reviewer": "R-101",
                "corrected_at": "2026-12-04T15:00:00+09:00",
                **outcome,
            },
            "idempotency_key": f"past-{uuid.uuid4().hex}",
        },
        user="R-101",
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _similar(pack: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in pack["records"] if r["role"] == "similar_case"]


def test_a_confirmed_outcome_is_remembered_and_proposes_nothing(world: World) -> None:
    customer = f"CUST-{uuid.uuid4().hex[:6]}"
    confirmed = _report(
        world,
        _case(world, customer, 660),
        result="confirmed",
        exception="bank_fee",
        selected_invoices=["INV-A100"],
        note_ja="手数料 660 円。いつも通り。",
    )

    later = _similar(_pack(world, _case(world, customer, 660)))

    assert confirmed["proposed_record_id"] is None and confirmed["review_case_id"] is None
    assert [s["record_id"] for s in later] == [confirmed["decision_record_id"]]
    summary = later[0]["body"]["case_summary"]
    assert summary == {
        "customer_id": customer,
        "amount": 549340,
        "gap": 660,
        "candidate_count": 1,
        "result": "confirmed",
        "exception": "bank_fee",
        "selected_invoices": ["INV-A100"],
        "reason": "手数料 660 円。いつも通り。",
    }


def test_a_corrected_outcome_is_remembered_and_still_suggests_a_rule(world: World) -> None:
    customer = f"CUST-{uuid.uuid4().hex[:6]}"
    corrected = _report(
        world,
        _case(world, customer, 440),
        result="corrected",
        exception="bank_fee",
        note_ja="手数料 440 円を差し引いた入金。",
    )

    later = _similar(_pack(world, _case(world, customer, 440)))

    assert corrected["proposed_record_id"]  # the learning loop still runs (D17 thresholds)
    assert [s["record_id"] for s in later] == [corrected["decision_record_id"]]


def test_the_closest_cases_come_first_and_never_another_customers(world: World) -> None:
    customer, other = f"CUST-{uuid.uuid4().hex[:6]}", f"CUST-{uuid.uuid4().hex[:6]}"
    far = _report(
        world, _case(world, customer, 5000), result="confirmed", exception="partial_payment"
    )
    near = _report(world, _case(world, customer, 700), result="confirmed", exception="bank_fee")
    _report(world, _case(world, other, 660), result="confirmed", exception="bank_fee")

    mine = [s["record_id"] for s in _similar(_pack(world, _case(world, customer, 660)))]

    assert mine == [near["decision_record_id"], far["decision_record_id"]]


def test_a_stored_pack_replays_its_cases_and_erasure_shreds_them(world: World) -> None:
    customer = f"CUST-{uuid.uuid4().hex[:6]}"
    past = _report(
        world,
        _case(world, customer, 660),
        result="confirmed",
        exception="bank_fee",
        note_ja="記録用。",
    )
    served = _pack(world, _case(world, customer, 660))
    replay = _call(
        world, f"/v1/cortex/context-packs/{served['pack_id']}", None, method="GET"
    ).json()
    assert _similar(replay) == _similar(served)

    with allow_network(), tenant_session(world.tenant) as s:
        erasure.erase_record(
            s,
            tenant_id=world.tenant,
            record_id=past["decision_record_id"],
            requested_by="it",
            reason="it",
        )
    after = _call(world, f"/v1/cortex/context-packs/{served['pack_id']}", None, method="GET").json()
    fresh = _similar(_pack(world, _case(world, customer, 660)))

    assert _similar(after)[0]["body"] is None and _similar(after)[0]["body_erased"] is True
    assert fresh == []
