"""Identity → tenant → OPA → RLS, end to end on real OPA and Postgres (issue 13).

Integration: real OPA with the real policy bundle, real Postgres RLS. The
Secrets Manager key store is replaced by known hashes here (its loading is
unit-tested with botocore's Stubber, and exercised live by `make dev`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from fastapi.testclient import TestClient

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.identity import ConsumerKeyStore
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, Status
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.policy_gate import PolicyGate

pytestmark = pytest.mark.integration

KEY = "it-askq2c-key"
OTHER_KEY = "it-other-key"
OPA = OrgBrainConfig().opa_url


def _record(seed: Seed, tenant_key: str, scope_key: str, sensitivity: str = "internal") -> str:
    tenant = next(t for t in seed.tenants if t.key == tenant_key)
    with tenant_session(tenant.tenant_id) as s:
        row = ledger.append_new(
            s,
            tenant_id=tenant.tenant_id,
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="test.rule/1",
                body={"text_ja": "統合テスト"},
                scope=seed.node_ids[scope_key],
                asserted_by={"type": "user", "id": "t"},
                authority=10,
                source={"system": "test"},
                valid_from=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
                enforcement="mandatory",
                sensitivity={"class": sensitivity, "personal_data": False},
            ),
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user": "OWN-001"},
        )
        return row.record_id


@pytest.fixture(scope="module")
def world() -> Iterator[dict[str, object]]:
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        seed = load_seed(Path(OrgBrainConfig().seed_dir))
        # A second consumer in the other tenant, for the cross-tenant check.
        other = next(t for t in seed.tenants if t.key == "other")
        other.opa_data["consumers"]["other-agent"] = {
            "purposes": {"invoice_drafting": {"sensitivity_ceiling": "confidential"}}
        }
        other.opa_data["grants"].append(
            {
                "id": "g-other-agent",
                "principal_type": "consumer",
                "principal_id": "other-agent",
                "role": "cortex_consumer",
                "node": other.tenant_id,
            }
        )
        seed.consumers["other-agent"] = type(seed.consumers["askq2c-agent"])(
            "other-agent", other.tenant_id, "other", ("cortex:read",)
        )
        register_tenants(seed)
        gate = PolicyGate(OpaClient(OPA, timeout_s=2.0), seed)
        gate.load_all()
        keys = ConsumerKeyStore(
            {
                "askq2c-agent": hashlib.sha256(KEY.encode()).hexdigest(),
                "other-agent": hashlib.sha256(OTHER_KEY.encode()).hexdigest(),
            }
        )
        api = TestClient(
            create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=gate))
        )
        yield {
            "api": api,
            "seed": seed,
            "gate": gate,
            "jp_record": _record(seed, "jp", "jp/finance/q2c-invoicing"),
            "restricted": _record(seed, "jp", "jp/finance", sensitivity="restricted"),
            "other_record": _record(seed, "other", "other/finance"),
        }
        app_engine().dispose()


def _get(world: dict[str, object], record: str, key: str = KEY, **params: str) -> httpx.Response:
    api: TestClient = world["api"]  # type: ignore[assignment]
    with allow_network():
        return api.get(
            f"/v1/cortex/records/{record}",
            params={"purpose": "invoice_drafting", **params},
            headers={"Authorization": f"Bearer {key}"},
        )


def test_an_allowed_read_carries_the_active_bundle_revision(world: dict[str, object]) -> None:
    with allow_network():
        bundle = httpx.post(f"{OPA}/v1/data/cortex/policy/revision", json={}).json()["result"]

    resp = _get(world, str(world["jp_record"]))

    assert resp.status_code == 200, resp.text
    assert resp.json()["policy_revision"] == bundle
    assert resp.json()["status"] == "approved"


def test_another_tenants_record_is_a_404_even_when_the_request_names_that_tenant(
    world: dict[str, object],
) -> None:
    seed: Seed = world["seed"]  # type: ignore[assignment]
    other_tenant = next(t.tenant_id for t in seed.tenants if t.key == "other")

    mine = _get(world, str(world["other_record"]), tenant_id=other_tenant)
    theirs = _get(world, str(world["jp_record"]), key=OTHER_KEY)

    assert mine.status_code == 404  # askq2c-agent (jp) asking for "other"'s record
    assert theirs.status_code == 404  # and the other way round
    assert _get(world, str(world["other_record"]), key=OTHER_KEY).status_code == 200


def test_policy_denials_are_403_with_the_reason(world: dict[str, object]) -> None:
    unregistered = _get(world, str(world["jp_record"]), purpose="marketing")
    too_sensitive = _get(world, str(world["restricted"]))

    assert unregistered.status_code == 403
    assert "is not registered" in unregistered.json()["detail"]["detail"]
    assert too_sensitive.status_code == 403
    assert "above this purpose's ceiling" in too_sensitive.json()["detail"]["detail"]


def test_an_unreachable_opa_is_a_503_never_a_read(world: dict[str, object]) -> None:
    seed: Seed = world["seed"]  # type: ignore[assignment]
    keys = ConsumerKeyStore({"askq2c-agent": hashlib.sha256(KEY.encode()).hexdigest()})
    dead_gate = PolicyGate(OpaClient("http://127.0.0.1:1", timeout_s=0.2), seed)
    api = TestClient(
        create_app(services=AppServices(seed=seed, consumer_keys=keys, policy_gate=dead_gate))
    )

    with allow_network():
        resp = api.get(
            f"/v1/cortex/records/{world['jp_record']}",
            params={"purpose": "invoice_drafting"},
            headers={"Authorization": f"Bearer {KEY}"},
        )

    assert resp.status_code == 503
    assert resp.json()["error"] == "access_gate_unavailable"


def test_after_opa_forgets_the_tenant_the_gate_reloads_it(world: dict[str, object]) -> None:
    seed: Seed = world["seed"]  # type: ignore[assignment]
    jp = next(t.tenant_id for t in seed.tenants if t.key == "jp")
    with allow_network():
        httpx.delete(
            f"{OPA}/v1/data/cortex/tenants/{jp}"
        ).raise_for_status()  # what an OPA restart does

    assert _get(world, str(world["jp_record"])).status_code == 200
