"""Erasure by crypto-shredding (issue 24) on real Postgres and the AWS
Emulator's KMS and S3. Each test uses a fresh tenant with its own KMS key, so
erasing it never touches the seed tenants other tests use.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import boto3
import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from sqlalchemy import text

from alembic import command
from app.cli import erase as erase_cli
from app.db import app_engine, tenant_session
from app.domain_packs.base import register_pack
from app.keys import KeyErasedError
from app.keys import install as install_keys
from app.models import Kind, Status
from app.projections import default_projection
from app.projections.base import SearchFilters, SearchQuery
from app.services import erasure, evidence, ledger
from app.services.access_gate import context_pack
from app.services.access_gate.applicability import CaseFacts
from app.services.body_codec import BodyUnreadableError, PlainJsonCodec, install_codec
from app.utils.business_dates import business_start
from tests.fakes.fake_pack import PACK

pytestmark = pytest.mark.integration

BUCKET = f"it-erasure-{uuid.uuid4().hex[:8]}"


@dataclass
class Tenant:
    tenant_id: str
    scope: str
    code: str


@pytest.fixture(scope="module")
def aws() -> Iterator[tuple[Any, Any]]:
    register_pack(PACK)
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        s3, kms = boto3.client("s3"), boto3.client("kms")
        s3.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
        evidence.install_archive(
            evidence.EvidenceArchive(s3, kms, retention_mode="COMPLIANCE", retention_days=1)
        )
        yield s3, kms
    install_codec(PlainJsonCodec(), default=True)  # the other test modules expect plain bodies
    app_engine().dispose()


@pytest.fixture
def tenant(aws: tuple[Any, Any]) -> Tenant:
    t = Tenant(str(uuid.uuid4()), str(uuid.uuid4()), f"W-{uuid.uuid4().hex[:8]}")
    with allow_network():
        install_keys([t.tenant_id], kms=aws[1])  # its own KEK; aes-gcm/1 for new bodies
    return t


def _record(
    t: Tenant, kind: Kind, text_ja: str, *, derived_from: tuple[str, ...] = (), **kw: Any
) -> str:
    body: dict[str, Any] = {"text_ja": text_ja}
    if kind is Kind.RULE:
        body |= {"targets": ["order.label"], "condition": f'order.widget_code == "{t.code}"'}
    with allow_network(), tenant_session(t.tenant_id) as s:
        return ledger.append_new(
            s,
            tenant_id=t.tenant_id,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": str(uuid.uuid4())},
            draft=ledger.RecordDraft(
                kind=kind,
                schema_version=f"widgets.{kind.value}/1",
                body=body,
                scope=t.scope,
                asserted_by={"type": "import", "id": "it"},
                authority=20,
                source={},
                valid_from=business_start(dt.date(2026, 1, 1)),
                enforcement="mandatory" if kind is Kind.RULE else None,
                entities=(f"widget:{t.code}",),
                assurance_level="reviewed",
                derived_from=derived_from,
                sensitivity={"class": "internal"},
                **kw,
            ),
        ).record_id


def _serve(t: Tenant) -> str:
    """A stored pack for this tenant's case (rules only: no search needed)."""
    case = {
        "order_id": "O-1",
        "widget_code": t.code,
        "colour": "赤",
        "qty": 3,
        "order_date": "2026-11-02",
    }
    with allow_network(), tenant_session(t.tenant_id) as s:
        built = context_pack.build_and_store(
            s,
            request=context_pack.PackRequest(
                tenant_id=t.tenant_id,
                consumer_id="it",
                end_user_id=None,
                trace_id="t",
                domain="widgets",
                case=case,
                scope=t.scope,
                purpose="p",
                as_of=dt.date(2026, 11, 2),
            ),
            pack=PACK,
            facts=CaseFacts(
                (f"widget:{t.code}",), {"order": {**case, "order_date": dt.date(2026, 11, 2)}}
            ),
            access=context_pack.Access((t.scope,), "confidential", "sha256:it"),
            search=default_projection(),
        )
        return built.pack_id


def _export(t: Tenant, pack_id: str) -> dict[str, Any]:
    with allow_network(), tenant_session(t.tenant_id) as s:
        stored = context_pack.load(s, pack_id)
        row, _ = evidence.request_export(s, stored=stored, bucket=BUCKET)
        done = evidence.run_export(s, row.export_id)
        assert done is not None
        return {"key": done.object_key, "sha": done.manifest_sha256}


def _bodies(t: Tenant, pack_id: str) -> dict[str, Any]:
    with allow_network(), tenant_session(t.tenant_id) as s:
        return {item.record_id: body for item, body in context_pack.load(s, pack_id).items}


def _open_body(t: Tenant, record_id: str) -> Any:
    with allow_network(), tenant_session(t.tenant_id) as s:
        return ledger.open_body(s, ledger.latest(s, record_id))


def _index(t: Tenant) -> None:
    with allow_network(), tenant_session(t.tenant_id) as s:
        default_projection().rebuild(s, t.tenant_id)


def _search(t: Tenant, words: str) -> set[str]:
    with allow_network(), tenant_session(t.tenant_id) as s:
        hits = default_projection().search(
            s, SearchQuery(text=words, filters=SearchFilters(statuses=("approved", "erased")))
        )
        return {h.record_id for h in hits}


def test_erasing_a_record_shreds_it_and_its_descendants_and_keeps_the_pack_whole(
    tenant: Tenant, aws: tuple[Any, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    s3, _ = aws
    erased = _record(tenant, Kind.RULE, "消去対象ルール 検収書を添付する")
    kept = _record(tenant, Kind.RULE, "残すルール 請求書番号を記載する")
    child = _record(tenant, Kind.DECISION, "子の決定", derived_from=(erased,))
    second_parent = _record(tenant, Kind.DECISION, "二番目の親", derived_from=(kept, erased))
    pack_id = _serve(tenant)
    exported = _export(tenant, pack_id)
    assert set(_bodies(tenant, pack_id)) == {erased, kept}

    code = erase_cli.main(
        [
            "record",
            "--tenant",
            tenant.tenant_id,
            "--record",
            erased,
            "--by",
            "ticket-APPI-1",
            "--reason",
            "本人からの削除請求",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert set(report["records"]) == {erased, child, second_parent}  # any parent position
    assert report["collateral"] == []
    assert set(report["keys_shredded"]) == {erased, child, second_parent}
    assert report["packs_affected"] == [pack_id]
    assert "検収書" not in json.dumps(report, ensure_ascii=False)  # ids and counts only
    bodies = _bodies(tenant, pack_id)
    assert bodies[erased] is None  # pack whole, that body gone
    assert bodies[kept] is not None and bodies[kept]["text_ja"].startswith("残すルール")
    for rid in (erased, child, second_parent):
        with pytest.raises(BodyUnreadableError):
            _open_body(tenant, rid)
    assert _open_body(tenant, kept)["text_ja"].startswith("残すルール")
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        statuses = dict(
            s.execute(text("SELECT record_id::text, status FROM memory_record_current")).all()
        )
        manifest = json.loads(
            evidence.archive().open(
                s3.get_object(Bucket=BUCKET, Key=exported["key"])["Body"].read()
            )
        )
        reopened = evidence.open_pack(s, manifest)
    assert statuses == {
        erased: "erased",
        child: "erased",
        second_parent: "erased",
        kept: "approved",
    }
    by_id = {r["record_id"]: r for r in reopened["records"]}
    assert by_id[erased]["body"] is None and by_id[erased]["body_erased"] is True
    assert by_id[kept]["body"]["text_ja"].startswith(
        "残すルール"
    )  # the archive still serves the rest


def test_reindex_after_erasure_does_not_bring_the_text_back(tenant: Tenant) -> None:
    gone = _record(tenant, Kind.FACT, "消去される事実 ゾウリムシ")
    stays = _record(tenant, Kind.FACT, "残る事実 ミドリムシ")
    _index(tenant)
    assert gone in _search(tenant, "ゾウリムシ")

    with allow_network(), tenant_session(tenant.tenant_id) as s:
        erasure.erase_record(
            s, tenant_id=tenant.tenant_id, record_id=gone, requested_by="it", reason="test"
        )
    _index(tenant)  # what `make reindex` does

    assert gone not in _search(tenant, "ゾウリムシ")
    assert stays in _search(tenant, "ミドリムシ")
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        text_left = s.execute(
            text("SELECT count(*) FROM search_doc WHERE search_text LIKE '%ゾウリムシ%'")
        ).scalar_one()
    assert text_left == 0


def test_erasing_a_record_twice_is_harmless(tenant: Tenant) -> None:
    rid = _record(tenant, Kind.FACT, "二度消し")
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        erasure.erase_record(
            s, tenant_id=tenant.tenant_id, record_id=rid, requested_by="it", reason="first"
        )
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        _, again = erasure.erase_record(
            s, tenant_id=tenant.tenant_id, record_id=rid, requested_by="it", reason="second"
        )
        versions = s.execute(
            text("SELECT count(*) FROM memory_record WHERE record_id = :r"), {"r": rid}
        ).scalar_one()
        log = s.execute(text("SELECT count(*) FROM erasure")).scalar_one()
    assert again.records == [rid] and again.keys_shredded == []
    assert versions == 2 and log == 2  # one erased version; both requests logged


def test_erasing_a_tenant_shreds_its_kms_key_bodies_and_exports(
    tenant: Tenant, aws: tuple[Any, Any]
) -> None:
    s3, kms = aws
    rid = _record(tenant, Kind.RULE, "テナントごと消える")
    pack_id = _serve(tenant)
    exported = _export(tenant, pack_id)
    _index(tenant)
    raw = s3.get_object(Bucket=BUCKET, Key=exported["key"])["Body"].read()
    assert evidence.archive().open(raw)  # opens before

    with allow_network(), tenant_session(tenant.tenant_id) as s:
        _, report = erasure.erase_tenant(
            s, tenant_id=tenant.tenant_id, kms=kms, requested_by="it", reason="契約終了"
        )

    assert report.kms["key_state"] == "PendingDeletion"
    assert rid in report.keys_shredded and report.projection_rows_deleted >= 1
    with allow_network(), pytest.raises(kms.exceptions.ClientError):
        evidence.archive().open(raw)  # Decrypt of the export's data key is refused
    with pytest.raises((KeyErasedError, BodyUnreadableError)):
        _open_body(tenant, rid)
    assert _bodies(tenant, pack_id)[rid] is None
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        assert s.execute(text("SELECT count(*) FROM search_doc")).scalar_one() == 0
        logged = s.execute(text("SELECT scope, report FROM erasure")).one()
    assert logged.scope == "tenant" and logged.report["kms"]["key_state"] == "PendingDeletion"


def test_a_tenant_erasure_needs_confirmation(
    tenant: Tenant, capsys: pytest.CaptureFixture[str]
) -> None:
    code = erase_cli.main(["tenant", "--tenant", tenant.tenant_id, "--by", "x", "--reason", "y"])

    assert code == 2
    assert "pass --yes" in capsys.readouterr().err


def test_erasing_an_unknown_record_says_so(
    tenant: Tenant, capsys: pytest.CaptureFixture[str]
) -> None:
    code = erase_cli.main(
        [
            "record",
            "--tenant",
            tenant.tenant_id,
            "--record",
            str(uuid.uuid4()),
            "--by",
            "x",
            "--reason",
            "y",
        ]
    )

    assert code == 1
    assert "nothing to erase" in capsys.readouterr().err


def test_an_erased_proposal_still_lists_with_no_title_or_body(tenant: Tenant) -> None:
    """A review case whose proposal was erased stays listable (issue 29)."""
    from app.services import review

    rid = _record(tenant, Kind.FACT, "消える提案")
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        assert review.title(s, ledger.latest(s, rid)) == "消える提案"
        erasure.erase_record(
            s, tenant_id=tenant.tenant_id, record_id=rid, requested_by="it", reason="t"
        )
    with allow_network(), tenant_session(tenant.tenant_id) as s:
        row = ledger.latest(s, rid)
        assert review.title(s, row) == review.ERASED_TITLE
        assert review.body_or_none(s, row) is None
