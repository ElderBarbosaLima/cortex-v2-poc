"""The review queue end to end (issue 18): a named owner approves or rejects;
OPA decides who may; nothing is written when it says no or is down. Real OPA
and Postgres; the fake "widgets" pack.
"""

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
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration

AGENT_KEY, DESK_KEY = "it-review-agent-key", "it-review-desk-key"


def _keys() -> ConsumerKeyStore:
    return ConsumerKeyStore(
        {
            "askq2c-agent": hashlib.sha256(AGENT_KEY.encode()).hexdigest(),
            "decision-desk": hashlib.sha256(DESK_KEY.encode()).hexdigest(),
        }
    )


@dataclass
class World:
    api: TestClient
    seed: Seed
    cfg: OrgBrainConfig

    @property
    def tenant(self) -> str:
        return self.seed.consumers["askq2c-agent"].tenant_id

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
        services = AppServices(seed=seed, consumer_keys=_keys(), policy_gate=gate)
        yield World(TestClient(create_app(services=services)), seed, cfg)
        app_engine().dispose()


def _call(
    api: TestClient, method: str, path: str, *, key: str, user: str | None, **kw: Any
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"}
    if user:
        headers["X-End-User-Id"] = user
    with allow_network():
        return api.request(method, path, headers=headers, **kw)


def _propose(w: World, code: str, *, by: str = "R-001", **source: Any) -> dict[str, Any]:
    """A rule proposal about widget `code` (unique per test: its own conflict)."""
    payload = {
        "domain": "widgets",
        "kind": "rule",
        "schema_version": "widgets.rule/1",
        "body": {
            "text_ja": f"{code} の注文には取扱注意ラベルを付ける。",
            "targets": ["order.label"],
            "condition": f'order.widget_code == "{code}"',
            "label": "取扱注意",
        },
        "scope": w.seed.node_ids["jp/finance/q2c-invoicing"],
        "entities": [f"widget:{code}"],
        "enforcement": "default",
        "authority": 10,
        "sensitivity": {"class": "internal"},
        "purpose": "invoice_drafting",
        "valid_from": "2026-11-01",
        "source": source,
    }
    resp = _call(
        w.api, "POST", "/v1/cortex/records", key=AGENT_KEY, user=w.person(by), json=payload
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _decide(
    w: World,
    case: str,
    action: str,
    *,
    user: str | None,
    api: TestClient | None = None,
    **body: Any,
) -> httpx.Response:
    return _call(
        api or w.api,
        "POST",
        f"/v1/cortex/review-cases/{case}/{action}",
        key=DESK_KEY,
        user=user,
        json=body,
    )


def _versions(w: World, record_id: str) -> list[tuple[int, str, Any]]:
    with allow_network(), tenant_session(w.tenant) as s:
        rows = s.execute(
            text(
                "SELECT version, status, approved_by FROM memory_record "
                "WHERE record_id = :r ORDER BY version"
            ),
            {"r": record_id},
        ).all()
    return [(r.version, r.status, r.approved_by) for r in rows]


def _case_status(w: World, case: str) -> tuple[str, int]:
    with allow_network(), tenant_session(w.tenant) as s:
        status = s.execute(
            text("SELECT status FROM review_case WHERE case_id = :c"), {"c": case}
        ).scalar_one()
        decisions = s.execute(
            text("SELECT count(*) FROM review_decision WHERE case_id = :c"), {"c": case}
        ).scalar_one()
    return status, int(decisions)


def test_the_corrector_cannot_approve_and_nothing_is_written(world: World) -> None:
    proposal = _propose(world, _code(), by="R-001")

    resp = _decide(
        world, proposal["review_case_id"], "approve", user=world.person("R-001"), authority=20
    )

    assert resp.status_code == 403
    assert resp.json()["error"] == "access_denied"
    assert resp.json()["detail"] == (
        "separation of duties: a contributor to this record cannot decide on it"
    )
    assert resp.json()["policy_revision"].startswith("sha256:")
    assert _versions(world, proposal["record_id"]) == [(1, "proposed", None)]
    assert _case_status(world, proposal["review_case_id"]) == ("open", 0)


def test_a_contributor_named_in_the_evidence_cannot_decide_either(world: World) -> None:
    # The backup corrected the case the proposal came from: they may not approve it.
    proposal = _propose(world, _code(), contributors=[world.person("OWN-002")])

    resp = _decide(
        world, proposal["review_case_id"], "approve", user=world.person("OWN-002"), authority=20
    )

    assert resp.status_code == 403
    assert "separation of duties" in resp.json()["detail"]


def test_an_agent_alone_cannot_decide(world: World) -> None:
    proposal = _propose(world, _code())

    resp = _decide(world, proposal["review_case_id"], "approve", user=None, authority=20)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "only a person can approve or reject"


def test_the_named_owner_approves_with_final_authority_and_a_decision(world: World) -> None:
    code = _code()
    first = _propose(world, code, by="R-001", case_ref="INV-2026-0001")
    second = _propose(world, code, by="R-002", case_ref="INV-2026-0002")  # same conflict
    case = first["review_case_id"]
    owner = world.person("OWN-001")

    resp = _decide(
        world,
        case,
        "approve",
        user=owner,
        record_id=first["record_id"],
        authority=25,
        enforcement="mandatory",
        reason="IBM 責任者確認済み",
    )

    assert resp.status_code == 200, resp.text
    result = resp.json()
    decision_id = result["decision_id"]
    assert {(r["record_id"], r["status"], r["outcome"]) for r in result["records"]} == {
        (first["record_id"], "approved", "approved"),
        (second["record_id"], "superseded", "superseded"),
    }
    assert _versions(world, first["record_id"])[-1] == (
        2,
        "approved",
        {"approver_type": "user", "user_id": owner, "decision_id": decision_id},
    )
    assert _case_status(world, case) == ("decided", 1)
    with allow_network(), tenant_session(world.tenant) as s:
        rec = s.execute(
            text("SELECT authority, enforcement FROM memory_record_current WHERE record_id = :r"),
            {"r": first["record_id"]},
        ).one()
        decision = s.execute(
            text(
                "SELECT kind, status, derived_from::text[] AS derived_from "
                "FROM memory_record WHERE record_id = :r"
            ),
            {"r": decision_id},
        ).one()
    assert (rec.authority, rec.enforcement) == (25, "mandatory")
    assert (decision.kind, decision.status) == ("decision", "approved")
    assert set(decision.derived_from) == {first["record_id"], second["record_id"]}

    again = _decide(world, case, "approve", user=owner, authority=25)
    assert again.status_code == 409
    assert again.json()["error"] == "case_decided"


def test_the_backup_rejects_and_the_outcome_is_rejected(world: World) -> None:
    proposal = _propose(world, _code(), case_ref="INV-2026-0003")
    case = proposal["review_case_id"]

    resp = _decide(world, case, "reject", user=world.person("OWN-002"), reason="根拠不足")

    assert resp.status_code == 200, resp.text
    assert [(r["status"], r["outcome"]) for r in resp.json()["records"]] == [
        ("deprecated", "rejected")
    ]
    history = _versions(world, proposal["record_id"])
    assert [s for _, s, _ in history] == ["proposed", "deprecated"]
    assert all(approved_by is None for _, _, approved_by in history)  # never approved

    detail = _call(
        world.api,
        "GET",
        f"/v1/cortex/review-cases/{case}",
        key=DESK_KEY,
        user=world.person("OWN-002"),
        params={"purpose": "rule_review"},
    ).json()
    assert detail["status"] == "decided"
    assert detail["proposals"][0]["outcome"] == "rejected"
    assert detail["proposals"][0]["body"]["text_ja"].endswith("取扱注意ラベルを付ける。")
    assert detail["affected_cases"] == ["INV-2026-0003"]
    assert detail["contributors"] == [world.person("R-001")]
    assert [(d["decision"], d["reason"], d["decided_by"]) for d in detail["decisions"]] == [
        ("reject", "根拠不足", world.person("OWN-002"))
    ]


def test_a_rejection_needs_a_reason(world: World) -> None:
    proposal = _propose(world, _code())

    resp = _decide(world, proposal["review_case_id"], "reject", user=world.person("OWN-001"))

    assert resp.status_code == 422
    assert _case_status(world, proposal["review_case_id"]) == ("open", 0)


def test_approval_with_opa_down_is_503_and_nothing_is_written(world: World) -> None:
    proposal = _propose(world, _code())
    dead_gate = PolicyGate(OpaClient("http://127.0.0.1:1", timeout_s=0.5), world.seed)
    services = AppServices(seed=world.seed, consumer_keys=_keys(), policy_gate=dead_gate)
    dead = TestClient(create_app(services=services))

    resp = _decide(
        world,
        proposal["review_case_id"],
        "approve",
        user=world.person("OWN-001"),
        authority=20,
        api=dead,
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "access_gate_unavailable"
    assert _versions(world, proposal["record_id"]) == [(1, "proposed", None)]
    assert _case_status(world, proposal["review_case_id"]) == ("open", 0)


def test_the_queue_lists_open_cases_with_title_and_named_owners(world: World) -> None:
    code = _code()
    proposal = _propose(world, code)

    page = _call(
        world.api,
        "GET",
        "/v1/cortex/review-cases",
        key=DESK_KEY,
        user=world.person("OWN-001"),
        params={
            "purpose": "rule_review",
            "scope": world.seed.node_ids["jp/finance/q2c-invoicing"],
            "limit": 100,
        },
    )

    assert page.status_code == 200, page.text
    mine = [c for c in page.json()["items"] if c["case_id"] == proposal["review_case_id"]]
    assert len(mine) == 1
    assert mine[0]["title"] == f"{code} の注文には取扱注意ラベルを付ける。"
    assert mine[0]["proposal_count"] == 1
    assert mine[0]["named_owners"] == {
        "owner": world.person("OWN-001"),
        "backup": world.person("OWN-002"),
    }
    assert all(c["status"] == "open" for c in page.json()["items"])


def test_the_title_is_not_stored_in_clear(world: World) -> None:
    with allow_network(), tenant_session(world.tenant) as s:
        columns = s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'review_case'"
            )
        ).scalars()
        assert "title" not in set(columns)


def test_the_case_says_ahead_who_may_decide_and_why_not(world: World) -> None:
    proposal = _propose(world, _code(), by="R-001")
    case = proposal["review_case_id"]

    def can(user: str | None) -> dict[str, Any]:
        resp = _call(
            world.api,
            "GET",
            f"/v1/cortex/review-cases/{case}",
            key=DESK_KEY,
            user=world.person(user) if user else None,
            params={"purpose": "rule_review"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["can_decide"]

    corrector, owner, agent = can("R-001"), can("OWN-001"), can(None)
    assert corrector["approve"]["allowed"] is False
    assert corrector["approve"]["reason"] == (
        "separation of duties: a contributor to this record cannot decide on it"
    )
    assert owner["approve"]["allowed"] is True and owner["reject"]["allowed"] is True
    assert agent["approve"]["reason"] == "only a person can approve or reject"

    _decide(world, case, "reject", user=world.person("OWN-001"), reason="不要")
    assert can("OWN-001")["approve"] == {
        "allowed": False,
        "reason": "this case is already decided",
        "policy_revision": None,
    }


def test_the_named_owner_retires_an_approved_rule_and_others_cannot(world: World) -> None:
    proposal = _propose(world, _code(), by="R-001")
    approved = _decide(
        world, proposal["review_case_id"], "approve", user=world.person("OWN-001"), authority=20
    )
    assert approved.status_code == 200, approved.text
    rid = proposal["record_id"]

    def retire(user: str | None, reason: str = "顧客要件の廃止") -> httpx.Response:
        return _call(
            world.api,
            "POST",
            f"/v1/cortex/records/{rid}/retire",
            key=DESK_KEY,
            user=world.person(user) if user else None,
            json={"reason": reason},
        )

    by_reviewer, by_agent = retire("R-001"), retire(None)
    done = retire("OWN-002")
    again = retire("OWN-001")

    assert by_reviewer.status_code == 403 and by_agent.status_code == 403
    assert by_agent.json()["detail"] == "only a person can approve or reject"
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["outcome"]) == ("deprecated", "retired")
    assert (again.status_code, again.json()["error"]) == (409, "not_retirable")
    assert [s for _, s, _ in _versions(world, rid)] == ["proposed", "approved", "deprecated"]
