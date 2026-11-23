"""Evidence export end to end (issue 23): a served pack's manifest, encrypted
under the tenant's KMS key, archived to an Object Lock bucket on the AWS
Emulator through the outbox. Real Postgres, OPA, S3 and KMS; fake pack.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.clients.opa import OpaClient
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.identity import ConsumerKeyStore
from app.keys import KmsKeyService
from app.main import AppServices, create_app, register_tenants
from app.models import Kind, Status
from app.seed.tenants import Seed, load_seed
from app.services import ledger
from app.services.body_codec import canonical
from app.services.evidence import EvidenceArchive, install_archive, open_pack
from app.services.policy_gate import PolicyGate
from app.utils.business_dates import business_start
from app.workers import handlers  # noqa: F401 -- registers the outbox handlers
from app.workers.outbox import drain
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration

KEYS = {"askq2c-agent": "it-ev-agent-key", "other-agent": "it-ev-other-key"}
BUCKET = f"it-evidence-{uuid.uuid4().hex[:8]}"


@dataclass
class World:
    api: TestClient
    seed: Seed
    s3: Any
    archive: EvidenceArchive

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
        s3, kms = boto3.client("s3"), boto3.client("kms")
        KmsKeyService(kms).ensure_tenant_key(seed.consumers["askq2c-agent"].tenant_id)
        s3.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
        archive = EvidenceArchive(s3, kms, retention_mode="COMPLIANCE", retention_days=1)
        install_archive(archive)
        keys = ConsumerKeyStore(
            {c: hashlib.sha256(k.encode()).hexdigest() for c, k in KEYS.items()}
        )
        services = AppServices(
            seed=seed, consumer_keys=keys, policy_gate=gate, evidence_bucket=BUCKET
        )
        yield World(TestClient(create_app(services=services)), seed, s3, archive)
        app_engine().dispose()


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


def _http(
    w: World, method: str, path: str, body: dict[str, Any] | None = None, key: str = "askq2c-agent"
) -> httpx.Response:
    with allow_network():
        return w.api.request(
            method, path, json=body, headers={"Authorization": f"Bearer {KEYS[key]}"}
        )


def _served_pack(w: World) -> dict[str, Any]:
    code = _code()
    with allow_network(), tenant_session(w.tenant) as s:
        ledger.append_new(
            s,
            tenant_id=w.tenant,
            status=Status.APPROVED,
            approved_by={
                "approver_type": "user",
                "user_id": w.seed.people["OWN-001"],
                "decision_id": None,
            },
            draft=ledger.RecordDraft(
                kind=Kind.RULE,
                schema_version="widgets.rule/1",
                body={
                    "text_ja": f"{code} は検品必須。",
                    "targets": ["order.label"],
                    "condition": f'order.widget_code == "{code}"',
                },
                scope=w.team,
                asserted_by={"type": "import", "id": "it"},
                authority=20,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement="mandatory",
                entities=(f"widget:{code}",),
                assurance_level="reviewed",
                derived_from=(),
                sensitivity={"class": "internal"},
            ),
        )
    resp = _http(
        w,
        "POST",
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
    pack = resp.json()
    verdict = _http(
        w,
        "POST",
        "/v1/cortex/verify",
        {"pack_id": pack["pack_id"], "case": _case(code), "output": {}},
    )
    assert verdict.json()["result"] == "pass"
    return pack


def _export(w: World, pack_id: str) -> dict[str, Any]:
    resp = _http(w, "POST", f"/v1/cortex/context-packs/{pack_id}/export")
    assert resp.status_code == 202, resp.text
    return resp.json()


def _run_worker(w: World) -> None:
    with allow_network():
        drain(app_engine(), w.tenant, limit=1000)


def _versions(w: World, key: str) -> list[dict[str, Any]]:
    with allow_network():
        return w.s3.list_object_versions(Bucket=BUCKET, Prefix=key).get("Versions", [])


def test_the_exported_object_opens_and_its_hash_matches_the_pack(world: World) -> None:
    pack = _served_pack(world)
    requested = _export(world, pack["pack_id"])
    assert requested["status"] == "pending"
    assert requested["object_key"] == f"tenants/{world.tenant}/context-packs/{pack['pack_id']}.json"

    _run_worker(world)
    done = _http(world, "GET", f"/v1/cortex/context-packs/{pack['pack_id']}/export").json()

    assert done["status"] == "exported" and done["retention_mode"] == "COMPLIANCE"
    with allow_network():
        raw = world.s3.get_object(Bucket=BUCKET, Key=done["object_key"])["Body"].read()
        manifest_bytes = world.archive.open(raw)
    assert "検品必須" not in raw.decode()  # encrypted at rest
    assert hashlib.sha256(manifest_bytes).hexdigest() == done["manifest_sha256"]
    manifest = json.loads(manifest_bytes)
    replay = _http(world, "GET", f"/v1/cortex/context-packs/{pack['pack_id']}").json()
    assert manifest["pack_sha256"] == hashlib.sha256(canonical(replay)).hexdigest()
    assert replay == pack
    # Bodies are kept sealed under each record's own key (issue 24), even inside
    # the encrypted manifest; while the keys exist, the pack rebuilds exactly.
    assert "検品必須" not in manifest_bytes.decode()
    assert all(r["body"] is None and r["body_sealed"] for r in manifest["pack"]["records"])
    with allow_network(), tenant_session(world.tenant) as s:
        assert open_pack(s, manifest) == replay
    assert manifest["policy_revision"] == pack["policy_revision"]
    assert [(r["record_id"], r["version"]) for r in manifest["records"]] == [
        (r["record_id"], r["version"]) for r in pack["records"]
    ]
    assert manifest["records"][0]["approved_by"]["user_id"] == world.seed.people["OWN-001"]
    assert [v["result"] for v in manifest["verifications"]] == ["pass"]


def test_deleting_the_version_under_retention_is_refused(world: World) -> None:
    pack = _served_pack(world)
    _export(world, pack["pack_id"])
    _run_worker(world)
    done = _http(world, "GET", f"/v1/cortex/context-packs/{pack['pack_id']}/export").json()

    with allow_network(), pytest.raises(ClientError) as refused:
        world.s3.delete_object(Bucket=BUCKET, Key=done["object_key"], VersionId=done["version_id"])

    assert refused.value.response["Error"]["Code"] == "AccessDenied"
    with allow_network():
        retention = world.s3.get_object_retention(
            Bucket=BUCKET, Key=done["object_key"], VersionId=done["version_id"]
        )["Retention"]
    assert retention["Mode"] == "COMPLIANCE"
    assert [v["VersionId"] for v in _versions(world, done["object_key"])] == [done["version_id"]]


def test_exporting_again_is_idempotent(world: World) -> None:
    pack = _served_pack(world)
    first = _export(world, pack["pack_id"])
    second = _export(world, pack["pack_id"])
    _run_worker(world)
    third = _export(world, pack["pack_id"])
    _run_worker(world)

    assert first["export_id"] == second["export_id"] == third["export_id"]
    assert third["status"] == "exported"
    with allow_network(), tenant_session(world.tenant) as s:
        events = s.execute(
            text("SELECT count(*) FROM outbox WHERE payload->>'export_id' = :e"),
            {"e": first["export_id"]},
        ).scalar_one()
    assert events == 1
    assert len(_versions(world, third["object_key"])) == 1


def test_a_retried_put_reuses_the_locked_object(world: World) -> None:
    pack = _served_pack(world)
    _export(world, pack["pack_id"])
    _run_worker(world)
    done = _http(world, "GET", f"/v1/cortex/context-packs/{pack['pack_id']}/export").json()
    with allow_network(), tenant_session(world.tenant) as s:
        from app.services.access_gate import context_pack
        from app.services.evidence import build_manifest, find_export, pack_view

        stored = context_pack.load(s, pack["pack_id"])
        row = find_export(s, pack["pack_id"])
        assert row is not None
        again = world.archive.put(row, build_manifest(s, stored, pack_view(stored)))

    assert again.reused is True and again.manifest_sha256 == done["manifest_sha256"]
    assert len(_versions(world, done["object_key"])) == 1


def test_another_tenants_key_cannot_export_or_read_the_export(world: World) -> None:
    pack = _served_pack(world)

    export = _http(
        world, "POST", f"/v1/cortex/context-packs/{pack['pack_id']}/export", key="other-agent"
    )
    status = _http(
        world, "GET", f"/v1/cortex/context-packs/{pack['pack_id']}/export", key="other-agent"
    )

    assert export.status_code == status.status_code == 404
