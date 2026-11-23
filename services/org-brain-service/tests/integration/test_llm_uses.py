"""Both AI uses through the LiteLLM client seam (issue 30), with a fake model:
rule suggestion in writeback, and embeddings in the search projection.
Real OPA and Postgres; the fake "widgets" pack."""

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
from sqlalchemy import text

from alembic import command
from app.clients.llm import install_llm
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
from app.workers import handlers  # noqa: F401 -- registers the projection handler
from app.workers.outbox import drain
from tests.fakes.fake_llm import FakeLlm
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration
AGENT = "it-llm-agent-key"


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
        keys = ConsumerKeyStore({"askq2c-agent": hashlib.sha256(AGENT.encode()).hexdigest()})
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


def _case(code: str) -> dict[str, Any]:
    return {
        "order_id": "O-1",
        "widget_code": code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-02",
    }


def _post(w: World, path: str, body: dict[str, Any], user: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {AGENT}"}
    if user:
        headers["X-End-User-Id"] = w.seed.people[user]
    with allow_network():
        return w.api.post(path, json=body, headers=headers)


def _pack(w: World, code: str) -> dict[str, Any]:
    resp = _post(
        w,
        "/v1/cortex/context-packs",
        {
            "domain": "widgets",
            "case": _case(code),
            "scope": w.team,
            "purpose": "invoice_drafting",
            "as_of": "2026-11-02",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _correct(w: World, code: str) -> dict[str, Any]:
    pack = _pack(w, code)
    resp = _post(
        w,
        "/v1/cortex/corrections",
        {
            "pack_id": pack["pack_id"],
            "domain": "widgets",
            "case_id": "O-1",
            "case": _case(code),
            "correction": {
                "case_id": "O-1",
                "target": "order.label",
                "after": "取扱注意",
                "reviewer": "R-001",
            },
            "idempotency_key": f"idem-{uuid.uuid4().hex}",
        },
        user="R-001",
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _record(w: World, record_id: str) -> Any:
    with allow_network(), tenant_session(w.tenant) as s:
        row = ledger.latest(s, record_id)
        return row, ledger.open_body(s, row)


def test_a_valid_model_draft_becomes_the_proposal(world: World) -> None:
    code = _code()
    fake = FakeLlm(
        {
            "text_ja": f"{code} の注文には必ず「取扱注意」ラベルを付ける。",
            "targets": ["order.label"],
            "condition": f'order.widget_code == "{code}" && order.qty >= 1',
            "entities": [f"widget:{code}"],
        }
    )
    install_llm(fake, rule_suggestion=True)

    body = _correct(world, code)

    assert body["suggested_by"] == "model" and body["proposal_problems"] == []
    row, rule = _record(world, body["proposed_record_id"])
    assert row.asserted_by == {"type": "model", "id": "fake/sonnet"}
    assert (row.status, row.authority) == ("proposed", 10)  # a person still approves
    assert rule["condition"] == f'order.widget_code == "{code}" && order.qty >= 1'
    assert rule["label"] == "取扱注意"  # the pack's own fields are kept
    assert row.source["suggested_by"] == "model" and row.source["model"] == "fake/sonnet"
    assert code in fake.chats[0] and "order.widget_code" in fake.chats[0]  # the brief it was given


@pytest.mark.parametrize(
    ("answer", "code_prefix"),
    [
        (
            {
                "text_ja": "x",
                "targets": ["order.label"],
                "condition": 'order.widget_code == "W-0" &&',
                "entities": [],
            },
            "llm_invalid_condition",
        ),
        (
            {"text_ja": "x", "targets": ["order.price"], "condition": "true", "entities": []},
            "llm_unknown_target",
        ),
        ({"text_ja": "x"}, "llm_malformed"),
        (None, "llm_unavailable"),
    ],
    ids=["bad-cel", "unknown-target", "malformed", "down"],
)
def test_a_bad_or_missing_model_draft_falls_back_to_the_packs_draft(
    world: World, answer: dict[str, Any] | None, code_prefix: str
) -> None:
    install_llm(FakeLlm(answer), rule_suggestion=True)

    body = _correct(world, _code())

    assert body["suggested_by"] == "policy"  # never proposed from the model's bad draft
    assert body["proposal_problems"][0]["code"].startswith(code_prefix)
    row, _ = _record(world, body["proposed_record_id"])
    assert row.asserted_by["type"] == "policy"


def test_embeddings_bring_knowledge_that_shares_no_word_with_the_case(world: World) -> None:
    code = _code()
    fact_text = "在庫は残りわずか。発送前に倉庫へ確認する。"  # no word in common with the case

    def topic(text_: str) -> str:
        return "same-meaning" if code in text_ or "在庫は残りわずか" in text_ else text_

    def add_fact() -> str:
        with allow_network(), tenant_session(world.tenant) as s:
            return ledger.append_new(
                s,
                tenant_id=world.tenant,
                status=Status.APPROVED,
                approved_by={"approver_type": "user", "user_id": world.seed.people["OWN-001"]},
                draft=ledger.RecordDraft(
                    kind=Kind.FACT,
                    schema_version="widgets.fact/1",
                    body={"text_ja": fact_text},
                    scope=world.team,
                    asserted_by={"type": "import", "id": "it"},
                    authority=10,
                    source={},
                    valid_from=business_start(dt.date(2026, 1, 1)),
                    enforcement=None,
                    entities=(),
                    assurance_level="reviewed",
                    derived_from=(),
                    sensitivity={"class": "internal"},
                ),
            ).record_id

    def candidates(pack: dict[str, Any]) -> set[str]:
        return {r["record_id"] for r in pack["records"] if r["role"] == "candidate"}

    plain = add_fact()
    with allow_network():
        drain(app_engine(), world.tenant, limit=1000)
    assert plain not in candidates(_pack(world, code))  # by words alone: not found

    install_llm(FakeLlm(topic=topic), embeddings=True)
    embedded = add_fact()
    with allow_network():
        drain(app_engine(), world.tenant, limit=1000)
        with tenant_session(world.tenant) as s:
            dims = s.execute(
                text("SELECT vector_dims(embedding) FROM search_doc WHERE record_id = :r"),
                {"r": embedded},
            ).scalar_one()

    assert dims == 16
    assert embedded in candidates(_pack(world, code))  # by meaning, revalidated against the ledger
