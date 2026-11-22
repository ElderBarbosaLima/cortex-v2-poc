"""The admission gate end to end: API → validation → OPA → ledger → review queue
(issue 17). Real OPA and Postgres; the fake "widgets" pack (core tests never use
a real workflow).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
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

KEY = "it-admission-key"


@pytest.fixture(scope="module")
def world() -> Iterator[tuple[TestClient, Seed]]:
    register_pack(PACK)
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(cfg.seed_dir))
        register_tenants(seed)
        gate = PolicyGate(OpaClient(cfg.opa_url, timeout_s=2.0), seed)
        gate.load_all()
        keys = ConsumerKeyStore({"askq2c-agent": hashlib.sha256(KEY.encode()).hexdigest()})
        yield (
            TestClient(
                create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
            ),
            seed,
        )
        app_engine().dispose()


def _body(seed: Seed, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "domain": "widgets",
        "kind": "rule",
        "schema_version": "widgets.rule/1",
        "body": {
            "text_ja": "W-1 の注文には取扱注意ラベルを付ける。",
            "targets": ["order.label"],
            "condition": 'order.widget_code == "W-1"',
            "label": "取扱注意",
        },
        "scope": seed.node_ids["jp/finance/q2c-invoicing"],
        "entities": ["widget:W-1"],
        "enforcement": "default",
        "authority": 10,
        "sensitivity": {"class": "internal"},
        "purpose": "invoice_drafting",
        "valid_from": "2026-11-01",
    }
    payload.update(overrides)
    return payload


def _post(api: TestClient, payload: dict[str, Any], end_user: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {KEY}"}
    if end_user:
        headers["X-End-User-Id"] = end_user
    with allow_network():
        return api.post("/v1/cortex/records", json=payload, headers=headers)


def _counts(seed: Seed) -> tuple[int, int, int]:
    tenant = seed.consumers["askq2c-agent"].tenant_id
    with allow_network(), tenant_session(tenant) as s:
        return tuple(  # type: ignore[return-value]
            int(s.execute(text(f"SELECT count(*) FROM {t}")).scalar_one())
            for t in ("memory_record", "review_case", "review_case_item")
        )


def test_an_invalid_condition_is_422_and_nothing_is_written(world: tuple[TestClient, Seed]) -> None:
    api, seed = world
    before = _counts(seed)

    resp = _post(
        api, _body(seed, body={**_body(seed)["body"], "condition": 'order.widget_code == "W-1" &&'})
    )

    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_proposal"
    assert [p["code"] for p in resp.json()["detail"]] == ["invalid_condition"]
    assert _counts(seed) == before


def test_a_personal_name_as_entity_is_422(world: tuple[TestClient, Seed]) -> None:
    api, seed = world

    resp = _post(api, _body(seed, entities=["widget:W-1", "田中太郎"]))

    assert resp.status_code == 422
    assert "non_canonical_entity" in [p["code"] for p in resp.json()["detail"]]


def test_sensitivity_above_the_purpose_ceiling_is_403(world: tuple[TestClient, Seed]) -> None:
    api, seed = world
    before = _counts(seed)

    resp = _post(api, _body(seed, sensitivity={"class": "restricted"}))

    assert resp.status_code == 403
    assert "above the confidential ceiling" in resp.json()["detail"]
    assert _counts(seed) == before


def test_two_proposals_for_the_same_conflict_share_one_case(world: tuple[TestClient, Seed]) -> None:
    api, seed = world
    body = _body(
        seed,
        entities=["widget:W-2"],
        body={**_body(seed)["body"], "condition": 'order.widget_code == "W-2"'},
    )
    _, cases_before, items_before = _counts(seed)

    first = _post(api, body)
    second = _post(api, body)

    assert first.status_code == second.status_code == 201
    assert first.json()["status"] == "proposed"
    assert first.json()["grouped"] is False and second.json()["grouped"] is True
    assert first.json()["review_case_id"] == second.json()["review_case_id"]
    assert first.json()["record_id"] != second.json()["record_id"]
    _, cases_after, items_after = _counts(seed)
    assert (cases_after - cases_before, items_after - items_before) == (1, 2)


def test_an_agent_alone_proposes_as_a_model_under_the_ceiling(
    world: tuple[TestClient, Seed],
) -> None:
    api, seed = world

    too_high = _post(
        api,
        _body(
            seed,
            authority=20,
            entities=["widget:W-3"],
            body={**_body(seed)["body"], "condition": 'order.widget_code == "W-3"'},
        ),
    )
    by_owner = _post(
        api,
        _body(
            seed,
            authority=20,
            entities=["widget:W-3"],
            body={**_body(seed)["body"], "condition": 'order.widget_code == "W-3"'},
        ),
        end_user=seed.people["OWN-001"],
    )

    assert too_high.status_code == 422
    assert too_high.json()["detail"][0]["code"] == "model_ceiling"
    assert by_owner.status_code == 201
    assert by_owner.json()["status"] == "proposed"  # still a proposal: approval is issue 18


def test_a_proposal_lands_sealed_and_proposed_in_the_ledger(world: tuple[TestClient, Seed]) -> None:
    api, seed = world
    resp = _post(api, _body(seed, entities=["widget:W-1"]), end_user=seed.people["R-001"])
    tenant = seed.consumers["askq2c-agent"].tenant_id

    with allow_network(), tenant_session(tenant) as s:
        row = s.execute(
            text(
                "SELECT status, asserted_by, approved_by, authority FROM memory_record "
                "WHERE record_id = :r"
            ),
            {"r": resp.json()["record_id"]},
        ).one()

    assert resp.status_code == 201
    assert row.status == "proposed" and row.approved_by is None
    assert row.asserted_by == {"type": "user", "id": seed.people["R-001"]}
