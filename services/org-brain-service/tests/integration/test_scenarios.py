"""The six failure scenarios of Rev. B, one named test each, numbered as in the
architecture (issue 37). `make scenarios` runs them first; the two workflows'
golden sets and `make e2e` run 2, 3 and 4 again on real data.

1. An import carrying an expired offer does not renew an approval or make it publishable.
2. A local instruction cannot beat a global prohibition.
3. A mandatory rule missed by similarity search is still supplied.
4. A rule withdrawn while the projection lags is caught by the release check.
5. A pack requested after a model or index change is the stored one; a fresh
   pack says it is fresh.
6. One conflict met by hundreds of jobs is one review case, the affected work visible.
Real OPA + Postgres; the fake "widgets" pack; every test mints its own widget code.
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

from alembic import command
from app.clients.llm import install_llm
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, Status
from app.projections import default_projection
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start
from tests.fakes.fake_llm import FakeLlm
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration
AGENT, DESK = "it-scn-agent", "it-scn-desk"


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


@pytest.fixture(autouse=True)
def no_llm_after() -> Iterator[None]:
    yield
    install_llm(None)


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _case(code: str, order: str = "O-1", date: str = "2026-11-05") -> dict[str, Any]:
    return {"order_id": order, "widget_code": code, "colour": "赤", "qty": 3, "order_date": date}


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


def _rule(
    w: World,
    code: str,
    *,
    scope: str = "jp/finance/q2c-invoicing",
    enforcement: str = "mandatory",
    authority: int = 30,
    text_ja: str = "検品必須。",
    condition: str | None = None,
    valid_to: dt.date | None = None,
) -> str:
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
                    "text_ja": text_ja,
                    "targets": ["order.label"],
                    "condition": condition or f'order.widget_code == "{code}"',
                },
                scope=w.seed.node_ids[scope],
                asserted_by={"type": "import", "id": "it"},
                authority=authority,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                valid_to=business_start(valid_to) if valid_to else None,
                enforcement=enforcement,
                entities=(f"widget:{code}",),
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": "internal"},
            ),
        ).record_id


def _pack(w: World, code: str, order: str = "O-1", date: str = "2026-11-05") -> dict[str, Any]:
    resp = _call(
        w,
        "POST",
        "/v1/cortex/context-packs",
        body={
            "domain": "widgets",
            "case": _case(code, order, date),
            "scope": w.team,
            "purpose": "invoice_drafting",
            "as_of": date,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _roles(pack: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    return {
        r["record_id"]: (r["role"], r["overridden_by"])
        for r in pack["records"]
        if r["kind"] == "rule"
    }


def test_scenario_1_an_import_never_renews_an_approval_or_makes_an_expired_offer_apply(
    world: World,
) -> None:
    code = _code()
    offer_text = "早期支払割引 2%（10月末まで）"
    offer = _rule(
        world,
        code,
        enforcement="default",
        authority=20,
        text_ja=offer_text,
        valid_to=dt.date(2026, 10, 31),
    )

    def item(valid_to: str | None, text_ja: str = offer_text) -> dict[str, Any]:
        return {
            "domain": "widgets",
            "kind": "rule",
            "schema_version": "widgets.rule/1",
            "body": {
                "text_ja": text_ja,
                "targets": ["order.label"],
                "condition": f'order.widget_code == "{code}"',
            },
            "scope": world.team,
            "entities": [f"widget:{code}"],
            "enforcement": "default",
            "authority": 20,
            "sensitivity": {"class": "internal"},
            "purpose": "invoice_drafting",
            "valid_from": "2026-01-01",
            "valid_to": valid_to,
        }

    resp = _call(
        world,
        "POST",
        "/v1/cortex/imports",
        user="R-001",
        body={
            "source_system": "legacy-rule-list",
            "items": [
                item("2026-10-31"),
                item("2027-03-31"),
                item("2026-09-30", "旧キャンペーン 5%（9月末まで）"),
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    same, renewal, expired = resp.json()["items"]
    assert (same["status"], same["record_id"]) == ("duplicate", offer)  # nothing written
    assert renewal["status"] == "proposed" and renewal["expired"] is False
    assert renewal["supersedes"] == offer  # a change to review, never a silent renewal
    assert expired["status"] == "proposed" and expired["expired"] is True
    with allow_network(), tenant_session(world.tenant) as s:
        kept = ledger.latest(s, offer)
        imported = ledger.latest(s, renewal["record_id"])
    assert kept.valid_to == business_start(dt.date(2026, 10, 31))  # the approval was not extended
    assert (imported.status, imported.authority) == ("proposed", 10)  # never approved by an import
    # Even once an owner approves the expired item, it never applies to a current case.
    approved = _call(
        world,
        "POST",
        f"/v1/cortex/review-cases/{expired['review_case_id']}/approve",
        key=DESK,
        user="OWN-001",
        body={"record_id": expired["record_id"], "authority": 20},
    )
    assert approved.status_code == 200, approved.text
    today = dt.date.today().isoformat()
    rules = _roles(_pack(world, code, date=today))
    assert (
        offer not in rules
        and expired["record_id"] not in rules
        and renewal["record_id"] not in rules
    )


def test_scenario_2_a_local_instruction_cannot_beat_a_global_prohibition(world: World) -> None:
    code = _code()
    local = _rule(
        world, code, enforcement="default", authority=40, text_ja="「急ぎ」を付ける。"
    )  # highest authority
    ban = _rule(
        world,
        code,
        scope="jp",
        enforcement="prohibition",
        authority=10,
        text_ja="独自の文言を付けない。",
    )

    roles = _roles(_pack(world, code))

    assert roles[ban] == ("constraint", None)
    assert roles[local] == ("overridden", ban)


def test_scenario_3_a_mandatory_rule_missed_by_search_is_still_supplied(world: World) -> None:
    code = _code()
    rule = _rule(
        world,
        code,
        scope="jp",
        text_ja="契約第12条の支払条件を備考に記載する。",
        condition="order.qty >= 1",
    )

    pack = _pack(world, code)

    assert _roles(pack)[rule][0] == "constraint"
    assert not [r for r in pack["records"] if r["role"] == "candidate" and r["record_id"] == rule]


def test_scenario_4_a_rule_withdrawn_while_the_projection_lags_is_caught_at_release(
    world: World,
) -> None:
    code = _code()
    rule = _rule(world, code)
    pack = _pack(world, code)
    with allow_network(), tenant_session(world.tenant) as s:
        ledger.transition(
            s, record_id=rule, to=Status.DEPRECATED
        )  # outbox not drained: projection behind

    verdict = _call(
        world,
        "POST",
        "/v1/cortex/verify",
        body={"pack_id": pack["pack_id"], "case": _case(code), "output": {}},
    ).json()

    assert verdict["result"] == "hold"
    assert ("rule_no_longer_approved", rule) in {
        (r["reason"], r["record_id"]) for r in verdict["reasons"]
    }


def test_scenario_5_after_a_model_or_index_change_the_stored_pack_is_returned(world: World) -> None:
    code = _code()
    _rule(world, code)
    served = _pack(world, code)
    assert served["built_with"] == {"projection": "postgres-search/2", "embedding_model": None}

    install_llm(FakeLlm(), embeddings=True)  # the embedding model changes…
    with allow_network(), tenant_session(world.tenant) as s:
        default_projection().rebuild(s, world.tenant)  # …and the index is rebuilt

    replay = _call(world, "GET", f"/v1/cortex/context-packs/{served['pack_id']}").json()
    fresh = _pack(world, code)

    assert replay == served  # the pack as served, not a re-run
    assert fresh["pack_id"] != served["pack_id"]
    assert fresh["built_with"]["embedding_model"] == "fake/embedding-16"  # a fresh pack says so


def test_scenario_6_one_conflict_met_by_hundreds_of_jobs_is_one_review_case(world: World) -> None:
    code, jobs = _code(), 200
    cases: set[str] = set()
    for n in range(jobs):
        order = f"O-{n:04d}"
        pack = _pack(world, code, order=order)
        resp = _call(
            world,
            "POST",
            "/v1/cortex/corrections",
            user="R-001" if n % 2 else "R-002",
            body={
                "pack_id": pack["pack_id"],
                "domain": "widgets",
                "case_id": order,
                "case": _case(code, order),
                "correction": {
                    "case_id": order,
                    "target": "order.label",
                    "after": "取扱注意",
                    "reviewer": "R-001",
                },
                "idempotency_key": f"scn6-{code}-{n}",
            },
        )
        assert resp.status_code == 202, resp.text
        cases.add(resp.json()["review_case_id"])

    assert len(cases) == 1
    detail = _call(
        world,
        "GET",
        f"/v1/cortex/review-cases/{cases.pop()}",
        key=DESK,
        user="OWN-001",
        params={"purpose": "rule_review"},
    ).json()
    assert detail["proposal_count"] == jobs
    assert len(detail["affected_cases"]) == jobs and detail["affected_cases"][0] == "O-0000"
