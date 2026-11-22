"""Writeback end to end (issue 22): correction → decision → proposed rule →
named owner approves → the next applicable case's pack carries it. Real
Postgres + OPA; the fake "widgets" pack. Each test mints its own widget code.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
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
from app.seed.tenants import Seed, load_seed
from app.services.policy_gate import PolicyGate
from app.services.writeback import RuleSuggestionPolicy
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration

AGENT, DESK = "it-wb-agent-key", "it-wb-desk-key"


@dataclass
class World:
    demo: TestClient  # 1/1/30
    strict: TestClient  # 3/2/30
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

        def app(spec: str) -> TestClient:
            services = AppServices(
                seed=seed,
                consumer_keys=keys,
                policy_gate=gate,
                rule_suggestion=RuleSuggestionPolicy.from_spec(spec),
            )
            return TestClient(create_app(services=services))

        yield World(app("1/1/30"), app("3/2/30"), seed)
        app_engine().dispose()


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _case(code: str, order: str = "O-1") -> dict[str, Any]:
    return {
        "order_id": order,
        "widget_code": code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-02",
    }


def _call(
    api: TestClient, path: str, body: dict[str, Any], *, key: str = AGENT, user: str | None = None
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"}
    if user:
        headers["X-End-User-Id"] = user
    with allow_network():
        return api.post(path, json=body, headers=headers)


def _pack(w: World, case: dict[str, Any], api: TestClient | None = None) -> dict[str, Any]:
    resp = _call(
        api or w.demo,
        "/v1/cortex/context-packs",
        {
            "domain": "widgets",
            "case": case,
            "scope": w.team,
            "purpose": "invoice_drafting",
            "as_of": "2026-11-02",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _correct(
    w: World,
    case: dict[str, Any],
    *,
    by: str = "R-001",
    key: str | None = None,
    after: str = "取扱注意",
    api: TestClient | None = None,
    pack_id: str | None = None,
) -> httpx.Response:
    api = api or w.demo
    pack_id = pack_id or _pack(w, case, api)["pack_id"]
    return _call(
        api,
        "/v1/cortex/corrections",
        {
            "pack_id": pack_id,
            "domain": "widgets",
            "case_id": case["order_id"],
            "case": case,
            "correction": {
                "case_id": case["order_id"],
                "target": "order.label",
                "after": after,
                "reviewer": by,
            },
            "idempotency_key": key or f"idem-{uuid.uuid4().hex}",
        },
        user=w.person(by),
    )


def _count(w: World, table: str) -> int:
    with allow_network(), tenant_session(w.tenant) as s:
        return int(s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def test_one_correction_opens_one_review_case_at_1_1_30(world: World) -> None:
    resp = _correct(world, _case(_code()))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["rule_suggestion"]["met"] is True
    assert body["proposed_record_id"] and body["review_case_id"]
    with allow_network(), tenant_session(world.tenant) as s:
        decision = s.execute(
            text("SELECT kind, asserted_by FROM memory_record WHERE record_id = :r"),
            {"r": body["decision_record_id"]},
        ).one()
        proposal = s.execute(
            text(
                "SELECT status, authority, asserted_by, source, derived_from::text[] AS d "
                "FROM memory_record WHERE record_id = :r"
            ),
            {"r": body["proposed_record_id"]},
        ).one()
    assert decision.kind == "decision"
    assert decision.asserted_by == {"type": "user", "id": world.person("R-001")}
    assert (proposal.status, proposal.authority) == ("proposed", 10)
    assert proposal.asserted_by == {"type": "policy", "id": "d17-rule-suggestion"}
    assert proposal.source["contributors"] == [world.person("R-001")]
    assert proposal.d == [body["decision_record_id"]]


def test_the_same_idempotency_key_writes_nothing_twice(world: World) -> None:
    case = _case(_code())
    pack_id = _pack(world, case)["pack_id"]
    first = _correct(world, case, key="idem-retry-" + case["widget_code"], pack_id=pack_id)
    counts = [_count(world, t) for t in ("correction", "memory_record", "review_case_item")]

    again = _correct(world, case, key="idem-retry-" + case["widget_code"], pack_id=pack_id)
    other = _correct(
        world, case, key="idem-retry-" + case["widget_code"], pack_id=pack_id, after="別ラベル"
    )

    assert again.status_code == 202 and again.json()["replayed"] is True
    keep = ("correction_id", "decision_record_id", "proposed_record_id", "review_case_id")
    assert {k: again.json()[k] for k in keep} == {k: first.json()[k] for k in keep}
    assert [_count(world, t) for t in ("correction", "memory_record", "review_case_item")] == counts
    assert (other.status_code, other.json()["error"]) == (409, "idempotency_conflict")


def test_at_3_2_30_the_case_opens_only_on_the_third_case_with_two_correctors(
    world: World,
) -> None:
    code = _code()
    runs = [("O-1", "R-001"), ("O-2", "R-001"), ("O-3", "R-002")]
    results = [
        _correct(world, _case(code, order), by=by, api=world.strict).json() for order, by in runs
    ]

    progress = [
        (
            r["rule_suggestion"]["distinct_cases"],
            r["rule_suggestion"]["distinct_correctors"],
            r["rule_suggestion"]["met"],
            r["review_case_id"] is not None,
        )
        for r in results
    ]
    assert progress == [(1, 1, False, False), (2, 1, False, False), (3, 2, True, True)]
    with allow_network(), tenant_session(world.tenant) as s:
        source = s.execute(
            text("SELECT source FROM memory_record WHERE record_id = :r"),
            {"r": results[2]["proposed_record_id"]},
        ).scalar_one()
    assert source["case_refs"] == ["O-1", "O-2", "O-3"]
    assert source["contributors"] == sorted([world.person("R-001"), world.person("R-002")])


def test_the_approved_rule_reaches_the_next_case_and_not_an_unrelated_one(world: World) -> None:
    code, unrelated = _code(), _code()
    corrected = _correct(world, _case(code, "O-1"), by="OWN-002").json()
    case_id = corrected["review_case_id"]

    by_corrector = _call(
        world.demo,
        f"/v1/cortex/review-cases/{case_id}/approve",
        {"authority": 20},
        key=DESK,
        user=world.person("OWN-002"),
    )
    by_owner = _call(
        world.demo,
        f"/v1/cortex/review-cases/{case_id}/approve",
        {"authority": 20},
        key=DESK,
        user=world.person("OWN-001"),
    )
    assert by_corrector.status_code == 403  # the corrector never approves their own rule
    assert "separation of duties" in by_corrector.json()["detail"]
    assert by_owner.status_code == 200, by_owner.text

    rule = corrected["proposed_record_id"]
    next_pack = _pack(world, _case(code, "O-2"))
    other_pack = _pack(world, _case(unrelated, "O-9"))

    roles = {r["record_id"]: r for r in next_pack["records"]}
    assert roles[rule]["role"] == "applied"
    assert roles[rule]["body"]["label"] == "取扱注意"
    assert rule not in {r["record_id"] for r in other_pack["records"]}
    verdict = _call(
        world.demo,
        "/v1/cortex/verify",
        {"pack_id": next_pack["pack_id"], "case": _case(code, "O-2"), "output": {"label": "通常"}},
    )
    assert verdict.json()["result"] == "hold"  # the learned rule now checks the output


def test_a_correction_needs_the_reviewer_and_the_packs_case(world: World) -> None:
    case = _case(_code())
    pack_id = _pack(world, case)["pack_id"]
    base = {
        "pack_id": pack_id,
        "domain": "widgets",
        "case_id": "O-1",
        "case": case,
        "correction": {
            "case_id": "O-1",
            "target": "order.label",
            "after": "x",
            "reviewer": "R-001",
        },
        "idempotency_key": f"idem-{uuid.uuid4().hex}",
    }

    anonymous = _call(world.demo, "/v1/cortex/corrections", base)
    wrong_case = _call(
        world.demo,
        "/v1/cortex/corrections",
        {**base, "case": {**case, "qty": 9}, "idempotency_key": f"idem-{uuid.uuid4().hex}"},
        user=world.person("R-001"),
    )
    bad = _call(
        world.demo,
        "/v1/cortex/corrections",
        {**base, "correction": {"after": "x"}, "idempotency_key": f"idem-{uuid.uuid4().hex}"},
        user=world.person("R-001"),
    )

    assert (anonymous.status_code, anonymous.json()["error"]) == (422, "reviewer_required")
    assert (wrong_case.status_code, wrong_case.json()["error"]) == (422, "case_mismatch")
    assert (bad.status_code, bad.json()["error"]) == (422, "invalid_correction")


def test_concurrent_retries_of_one_key_write_one_correction(world: World) -> None:
    case = _case(_code())
    pack_id = _pack(world, case)["pack_id"]
    key = f"idem-race-{uuid.uuid4().hex}"
    before = _count(world, "correction")

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(
            pool.map(lambda _: _correct(world, case, key=key, pack_id=pack_id), range(4))
        )

    assert {r.status_code for r in replies} == {202}
    assert sorted(r.json()["replayed"] for r in replies) == [False, True, True, True]
    assert len({r.json()["correction_id"] for r in replies}) == 1
    assert _count(world, "correction") == before + 1
