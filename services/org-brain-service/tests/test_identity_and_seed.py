"""Tenant seed → OPA data, consumer identity, and the tenant coming from identity only."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, cast

import boto3
import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app
from app.seed.tenants import load_seed
from app.services.policy_gate import PolicyGate

SEED_DIR = Path(os.environ.get("CORTEX_SYNTHETIC_DIR", "/data/synthetic"))
KEY = "local-dev-askq2c-key"


class FakeGate:
    def read(self, payload: dict[str, Any]) -> Any:  # not reached by these routes
        raise AssertionError("no policy decision expected")


@pytest.fixture
def api() -> TestClient:
    seed = load_seed(SEED_DIR)
    keys = ConsumerKeyStore({"askq2c-agent": hashlib.sha256(KEY.encode()).hexdigest()})
    return TestClient(
        create_app(
            services=AppServices(
                seed=seed, consumer_keys=keys, policy_gate=cast(PolicyGate, FakeGate())
            )
        )
    )


def test_seed_builds_one_opa_document_per_tenant() -> None:
    seed = load_seed(SEED_DIR)
    jp = next(t for t in seed.tenants if t.key == "jp")
    doc = json.loads((SEED_DIR / "tenant.json").read_text())
    jp_node = next(n["obj_id"] for n in doc["nodes"] if n["key"] == "jp")
    invoicing = next(n["obj_id"] for n in doc["nodes"] if n["key"] == "jp/finance/q2c-invoicing")

    assert {t.key for t in seed.tenants} == {"jp", "other"}
    assert jp.tenant_id == jp_node
    assert len(jp.opa_data["nodes"][invoicing]["path_up"]) == 4  # team, finance, tenant, org root
    assert all(n["type"] != "tenant" or k == jp_node for k, n in jp.opa_data["nodes"].items())
    principals = {(g["principal_type"], g["role"]) for g in jp.opa_data["grants"]}
    assert ("consumer", "cortex_consumer") in principals and (
        "user",
        "final_validator",
    ) in principals
    assert not [g for g in jp.opa_data["grants"] if g["principal_id"] == seed.people["X-001"]]
    assert seed.consumers["askq2c-agent"].tenant_id == jp_node
    assert set(jp.opa_data["consumers"]["askq2c-agent"]["purposes"]) == {
        "invoice_drafting",
        "cash_matching",
    }


def test_key_hashes_load_from_secrets_manager() -> None:
    sm = boto3.client(
        "secretsmanager", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    secret = json.dumps({"askq2c-agent": {"sha256": hashlib.sha256(KEY.encode()).hexdigest()}})
    with Stubber(sm) as stub:
        stub.add_response(
            "get_secret_value",
            {"SecretString": secret},
            {"SecretId": "cortex-poc/consumer-api-keys"},
        )
        store = ConsumerKeyStore.from_secrets_manager("cortex-poc/consumer-api-keys", client=sm)

    assert store.consumer_for(KEY) == "askq2c-agent"
    assert store.consumer_for("wrong") is None


def test_whoami_takes_the_tenant_from_identity_never_from_the_request(api: TestClient) -> None:
    other = str(uuid.uuid4())

    resp = api.get(
        f"/v1/cortex/whoami?tenant_id={other}",
        headers={"Authorization": f"Bearer {KEY}", "X-Tenant-Id": other},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["consumer_id"] == "askq2c-agent"
    assert body["tenant_id"] == load_seed(SEED_DIR).consumers["askq2c-agent"].tenant_id != other


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer nope"}, 401),
        ({"Authorization": f"Basic {KEY}"}, 401),
        ({"Authorization": f"Bearer {KEY}", "X-End-User-Id": "OWN-001"}, 400),
    ],
)
def test_bad_credentials_and_headers_are_refused(
    api: TestClient, headers: dict[str, str], status: int
) -> None:
    assert api.get("/v1/cortex/whoami", headers=headers).status_code == status


def test_end_user_header_is_carried_as_a_uuid(api: TestClient) -> None:
    person = load_seed(SEED_DIR).people["OWN-001"]

    resp = api.get(
        "/v1/cortex/whoami",
        headers={"Authorization": f"Bearer {KEY}", "X-End-User-Id": person.upper()},
    )

    assert resp.json()["end_user_id"] == person
