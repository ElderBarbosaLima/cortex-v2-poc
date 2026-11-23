"""The Phase 1 integration profile (issue 36): a consumer with only
`cortex:pack` gets context packs and reads served ones back; every other
operation is 403 insufficient_scope. Real OPA + Postgres; fake pack."""

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

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine
from app.domain_packs.base import register_pack
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.seed.tenants import Seed, load_seed
from app.services.policy_gate import PolicyGate
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration
KEY = "it-ibm-pr-key"
CASE = {
    "order_id": "O-1",
    "widget_code": "W-1",
    "colour": "赤",
    "qty": 3,
    "order_date": "2026-11-02",
}
ANY_ID = "00000000-0000-0000-0000-000000000001"


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
        keys = ConsumerKeyStore({"ibm-pr-agent": hashlib.sha256(KEY.encode()).hexdigest()})
        yield (
            TestClient(
                create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
            ),
            seed,
        )
        app_engine().dispose()


def _call(
    api: TestClient, method: str, path: str, body: Any = None, params: dict[str, str] | None = None
) -> httpx.Response:
    with allow_network():
        return api.request(
            method, path, json=body, params=params, headers={"Authorization": f"Bearer {KEY}"}
        )


def test_the_ibm_consumer_gets_a_context_pack_and_reads_it_back(
    world: tuple[TestClient, Seed],
) -> None:
    api, seed = world
    scope = seed.node_ids["jp/finance/q2c-invoicing"]

    served = _call(
        api,
        "POST",
        "/v1/cortex/context-packs",
        {
            "domain": "widgets",
            "case": CASE,
            "scope": scope,
            "purpose": "payment_reconciliation",
            "as_of": "2026-11-02",
        },
    )
    replay = _call(api, "GET", f"/v1/cortex/context-packs/{served.json()['pack_id']}")

    assert served.status_code == 201, served.text
    assert replay.status_code == 200 and replay.json() == served.json()
    assert _call(api, "GET", "/v1/cortex/whoami").json()["scopes"] == ["cortex:pack"]


@pytest.mark.parametrize(
    ("method", "path", "body", "params"),
    [
        ("POST", "/v1/cortex/verify", {"pack_id": ANY_ID, "case": CASE, "output": {}}, None),
        (
            "POST",
            "/v1/cortex/corrections",
            {
                "pack_id": ANY_ID,
                "domain": "widgets",
                "case_id": "O-1",
                "case": CASE,
                "correction": {},
                "idempotency_key": "abcdefgh",
            },
            None,
        ),
        ("GET", "/v1/cortex/records", None, {"scope": ANY_ID, "purpose": "payment_reconciliation"}),
        ("POST", "/v1/cortex/records", {}, None),
        (
            "GET",
            "/v1/cortex/review-cases",
            None,
            {"scope": ANY_ID, "purpose": "payment_reconciliation"},
        ),
        ("POST", f"/v1/cortex/context-packs/{ANY_ID}/export", None, None),
        ("POST", f"/v1/cortex/records/{ANY_ID}/retire", {"reason": "x"}, None),
    ],
    ids=["verify", "corrections", "list-records", "propose", "review-queue", "export", "retire"],
)
def test_everything_else_is_out_of_the_profile(
    world: tuple[TestClient, Seed], method: str, path: str, body: Any, params: dict[str, str] | None
) -> None:
    api, _ = world

    resp = _call(api, method, path, body, params)

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "insufficient_scope"
