"""Regions, option (b) of ADR 0002 (issue 32). Two ledgers on the test Postgres:
`org_brain_hq` (the home region, "hq") and `org_brain` (Japan, the one the API
serves). The job copies HQ's published Global records (the tenant root) into
Japan and carries every later change across.

Acceptance (docs/tasks.md §32): a Global rule approved in HQ appears in a
Japan pack; retired in HQ, verify holds the Japan pack; a personal record
never replicates. Plus: idempotent, supersede and erasure propagate, and a
replica is read-only in Japan.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
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
from sqlalchemy import Engine, func, select, text

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, region_engine, tenant_session
from app.domain_packs.base import register_pack
from app.identity import ConsumerKeyStore
from app.keys import install as install_keys
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, MemoryRecord, Status
from app.seed.tenants import Seed, load_seed
from app.services import erasure, ledger, replication
from app.services.body_codec import PlainJsonCodec, install_codec
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration
AGENT, DESK = "it-reg-agent", "it-reg-desk"
HQ_DB = "org_brain_hq"


@dataclass
class Regions:
    api: TestClient  # Japan's API
    seed: Seed
    hq: Engine

    @property
    def tenant(self) -> str:
        return self.seed.consumers["askq2c-agent"].tenant_id

    def replicate(self) -> replication.Report:
        with (
            allow_network(),
            tenant_session(self.tenant, self.hq) as src,
            tenant_session(self.tenant) as dst,
        ):
            return replication.replicate(
                src,
                dst,
                tenant_id=self.tenant,
                region="hq",
                src_scope=self.tenant,
                dst_scope=self.tenant,
            )


def _migrate(database: str | None) -> None:
    before = os.environ.get("POSTGRES_DB")
    if database:
        os.environ["POSTGRES_DB"] = database  # alembic's env.py reads OrgBrainConfig()
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if before is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = before


@pytest.fixture(scope="module")
def regions() -> Iterator[Regions]:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    with allow_network():
        _migrate(None)
        _migrate(HQ_DB)
        seed = load_seed(Path(cfg.seed_dir))
        hq = region_engine(HQ_DB)
        register_tenants(seed)
        register_tenants(seed, hq)
        install_keys(t.tenant_id for t in seed.tenants)
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
        keys = ConsumerKeyStore(
            {
                "askq2c-agent": hashlib.sha256(AGENT.encode()).hexdigest(),
                "decision-desk": hashlib.sha256(DESK.encode()).hexdigest(),
            }
        )
        api = TestClient(
            create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
        )
        yield Regions(api, seed, hq)
        install_codec(PlainJsonCodec(), default=True)  # the other test modules expect plain bodies
        app_engine().dispose()
        hq.dispose()


def _code() -> str:
    return f"W-{uuid.uuid4().hex[:8]}"


def _case(code: str) -> dict[str, Any]:
    return {
        "order_id": "O-1",
        "widget_code": code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-05",
    }


def _hq_rule(
    r: Regions,
    code: str,
    *,
    scope: str | None = None,
    status: Status = Status.APPROVED,
    personal: bool = False,
    supersedes: str | None = None,
    text_ja: str = "グループ共通、検品必須。",
) -> str:
    with allow_network(), tenant_session(r.tenant, r.hq) as s:
        return ledger.append_new(
            s,
            tenant_id=r.tenant,
            status=status,
            approved_by=(
                {"approver_type": "user", "user_id": r.seed.people["OWN-001"]}
                if status is Status.APPROVED
                else None
            ),
            supersedes=supersedes,
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="widgets.rule/1",
                body={
                    "text_ja": text_ja,
                    "targets": ["order.label"],
                    "condition": f'order.widget_code == "{code}"',
                },
                scope=scope or r.tenant,  # the published Global scope: the tenant root
                asserted_by={"type": "user", "id": r.seed.people["OWN-001"]},
                authority=30,
                source={"origin": "hq-policy-team"},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement="mandatory",
                entities=(f"widget:{code}",),
                assurance_level="reviewed",
                sensitivity={"class": "internal", "personal_data": personal},
            ),
        ).record_id


def _hq_transition(r: Regions, record_id: str, to: Status) -> None:
    with allow_network(), tenant_session(r.tenant, r.hq) as s:
        ledger.transition(s, record_id=record_id, to=to)


def _call(r: Regions, method: str, path: str, body: Any = None, **kw: Any) -> httpx.Response:
    headers = {"Authorization": f"Bearer {kw.pop('key', AGENT)}"}
    if user := kw.pop("user", None):
        headers["X-End-User-Id"] = r.seed.people[user]
    with allow_network():
        return r.api.request(method, path, json=body, headers=headers, **kw)


def _pack(r: Regions, code: str) -> dict[str, Any]:
    resp = _call(
        r,
        "POST",
        "/v1/cortex/context-packs",
        {
            "domain": "widgets",
            "case": _case(code),
            "scope": r.seed.node_ids["jp/finance/q2c-invoicing"],
            "purpose": "invoice_drafting",
            "as_of": "2026-11-05",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rules(pack: dict[str, Any]) -> dict[str, str]:
    return {x["record_id"]: x["role"] for x in pack["records"] if x["kind"] == "rule"}


def _held_for(r: Regions, pack: dict[str, Any], code: str) -> set[tuple[str, str]]:
    verdict = _call(
        r,
        "POST",
        "/v1/cortex/verify",
        {"pack_id": pack["pack_id"], "case": _case(code), "output": {}},
    ).json()
    return {(x["reason"], x.get("record_id") or "") for x in verdict["reasons"]}


def _japan(r: Regions, record_id: str) -> MemoryRecord | None:
    with allow_network(), tenant_session(r.tenant) as s:
        try:
            return ledger.latest(s, record_id)
        except ledger.RecordNotFoundError:
            return None


def test_a_global_rule_approved_in_hq_reaches_a_japan_pack_as_a_read_only_replica(
    regions: Regions,
) -> None:
    code = _code()
    rule = _hq_rule(regions, code)
    assert rule not in _rules(_pack(regions, code))  # not there before the job runs

    report = regions.replicate()

    assert rule in report.published
    assert _rules(_pack(regions, code))[rule] == "constraint"  # same record_id, mandatory
    replica = _japan(regions, rule)
    assert replica is not None
    assert replica.asserted_by["type"] == "replication" and replica.asserted_by["id"] == "region:hq"
    assert replica.asserted_by["origin"]["type"] == "user"
    assert replica.source["replica_of"]["record_id"] == rule
    assert replica.key_ref != rule  # re-sealed under Japan's own data key
    with allow_network(), tenant_session(regions.tenant) as s:
        assert ledger.open_body(s, replica)["text_ja"] == "グループ共通、検品必須。"

    retire = _call(
        regions,
        "POST",
        f"/v1/cortex/records/{rule}/retire",
        {"reason": "local"},
        key=DESK,
        user="OWN-001",
    )
    assert retire.status_code == 409 and retire.json()["error"] == "replica_read_only"


def test_replication_is_idempotent(regions: Regions) -> None:
    _hq_rule(regions, _code())
    regions.replicate()
    with allow_network(), tenant_session(regions.tenant) as s:
        before = s.scalar(select(func.count()).select_from(MemoryRecord))

    again = regions.replicate()

    with allow_network(), tenant_session(regions.tenant) as s:
        after = s.scalar(select(func.count()).select_from(MemoryRecord))
    assert (again.published, again.updated, again.erased) == ([], [], [])
    assert after == before


def test_retired_in_hq_verify_holds_the_japan_pack_once_replicated(regions: Regions) -> None:
    code = _code()
    rule = _hq_rule(regions, code)
    regions.replicate()
    pack = _pack(regions, code)
    _hq_transition(regions, rule, Status.DEPRECATED)

    lagging = _held_for(regions, pack, code)  # the cost of option (b): until the job runs
    regions.replicate()
    held = _held_for(regions, pack, code)

    assert ("rule_no_longer_approved", rule) not in lagging
    assert ("rule_no_longer_approved", rule) in held
    assert rule not in _rules(_pack(regions, code))


def test_personal_proposed_and_team_records_never_leave_hq(regions: Regions) -> None:
    code = _code()
    personal = _hq_rule(regions, code, personal=True)
    proposed = _hq_rule(regions, code, status=Status.PROPOSED)
    team = _hq_rule(regions, code, scope=regions.seed.node_ids["jp/finance/q2c-invoicing"])

    report = regions.replicate()

    assert personal in report.withheld
    assert all(_japan(regions, x) is None for x in (personal, proposed, team))
    assert not {personal, proposed, team} & set(_rules(_pack(regions, code)))


def test_a_supersede_in_hq_carries_across(regions: Regions) -> None:
    code = _code()
    old = _hq_rule(regions, code)
    regions.replicate()
    with allow_network(), tenant_session(regions.tenant, regions.hq) as s:
        ledger.transition(s, record_id=old, to=Status.SUPERSEDED)
    new = _hq_rule(regions, code, supersedes=old, text_ja="グループ共通、検品と梱包確認が必須。")

    report = regions.replicate()

    assert (old, "superseded") in report.updated and new in report.published
    rules = _rules(_pack(regions, code))
    assert new in rules and old not in rules
    replica = _japan(regions, new)
    assert replica is not None and replica.supersedes == old


def test_an_erasure_in_hq_shreds_the_replica_in_japan(regions: Regions) -> None:
    code = _code()
    rule = _hq_rule(regions, code)
    regions.replicate()
    replica = _japan(regions, rule)
    assert replica is not None
    with allow_network(), tenant_session(regions.tenant, regions.hq) as s:
        erasure.erase_record(
            s, tenant_id=regions.tenant, record_id=rule, requested_by="it", reason="it"
        )

    report = regions.replicate()

    assert rule in report.erased
    erased = _japan(regions, rule)
    assert erased is not None and erased.status == "erased"
    with allow_network(), tenant_session(regions.tenant) as s:
        keys = s.execute(
            text("SELECT count(*) FROM record_key WHERE key_ref = CAST(:k AS uuid)"),
            {"k": replica.key_ref},
        ).scalar()
    assert keys == 0  # crypto-shredded in Japan too
    assert rule not in _rules(_pack(regions, code))
