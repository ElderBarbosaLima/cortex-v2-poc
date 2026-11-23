"""Bodies sealed with real KMS data keys, stored in real Postgres (issue 14).

Integration: KMS (through the AWS Emulator, via the standard SDK variables)
and Postgres RLS / privileges.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from alembic.config import Config
from arbitr_test_utils.network import allow_network
from sqlalchemy import Engine, create_engine, text

from alembic import command
from app.config import OrgBrainConfig
from app.db import tenant_session
from app.keys import AesGcmCodec, KmsKeyService, tenant_alias
from app.models import Kind, Status
from app.services import ledger
from app.services.body_codec import install_codec

pytestmark = pytest.mark.integration

TENANT = str(uuid.uuid4())  # a fresh tenant per run: the emulator keeps its keys in memory
SECRET_TEXT = "架空電機の請求書には検収書を添付する"
APP = Path(__file__).resolve().parents[2] / "app"


def _draft(**overrides: Any) -> ledger.RecordDraft:
    fields: dict[str, Any] = {
        "kind": Kind.RULE,
        "schema_version": "test.rule/1",
        "body": {"text_ja": SECRET_TEXT},
        "scope": TENANT,
        "asserted_by": {"type": "user", "id": "R-001"},
        "authority": 10,
        "source": {"system": "test"},
        "valid_from": dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        "enforcement": "mandatory",
    }
    fields.update(overrides)
    return ledger.RecordDraft(**fields)


@pytest.fixture(scope="module")
def stack() -> Iterator[tuple[Engine, Engine, KmsKeyService, AesGcmCodec, Any]]:
    cfg = OrgBrainConfig()
    with allow_network():
        command.upgrade(Config("alembic.ini"), "head")
        kms = boto3.client("kms")
        keys = KmsKeyService(kms)
        keys.ensure_tenant_key(TENANT)
        codec = AesGcmCodec(keys)
        install_codec(codec)  # open rows by their body_codec, as the service does at startup
        app, owner = create_engine(cfg.app_database_url), create_engine(cfg.owner_database_url)
        yield app, owner, keys, codec, kms
        app.dispose()
        owner.dispose()


def test_the_body_is_never_stored_in_clear_and_the_app_reads_it_back(stack: Any) -> None:
    app, owner, keys, codec, _ = stack
    with allow_network(), tenant_session(TENANT, app) as s:
        row = ledger.append_new(
            s,
            tenant_id=TENANT,
            draft=_draft(),
            codec=codec,
            status=Status.APPROVED,
            approved_by={"approver_type": "user", "user": "OWN-001"},
        )

    with owner.begin() as conn:  # the table owner sees exactly the stored bytes
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT})
        stored = conn.execute(
            text("SELECT body_sealed, body_codec FROM memory_record WHERE record_id = :r"),
            {"r": row.record_id},
        ).one()
        anywhere = conn.execute(
            text("SELECT count(*) FROM memory_record WHERE position(:p IN body_sealed) > 0"),
            {"p": SECRET_TEXT.encode()},
        ).scalar_one()
    keys.forget()  # read through KMS, not the cache
    with allow_network(), tenant_session(TENANT, app) as s:
        reopened = ledger.open_body(s, ledger.latest(s, row.record_id))

    assert stored.body_codec == "aes-gcm/1"
    assert SECRET_TEXT.encode() not in bytes(stored.body_sealed)
    assert anywhere == 0
    assert reopened == {"text_ja": SECRET_TEXT}


def test_every_record_gets_its_own_key_derived_ones_too(stack: Any) -> None:
    app, _, keys, codec, kms = stack
    with allow_network(), tenant_session(TENANT, app) as s:
        a = ledger.append_new(s, tenant_id=TENANT, draft=_draft(), codec=codec)
        b = ledger.append_new(s, tenant_id=TENANT, draft=_draft(), codec=codec)
        child = ledger.append_new(
            s, tenant_id=TENANT, draft=_draft(derived_from=(a.record_id,)), codec=codec
        )
        wrapped = dict(
            s.execute(
                text(
                    "SELECT key_ref::text, wrapped_dek FROM record_key "
                    "WHERE key_ref = ANY(CAST(:r AS uuid[]))"
                ),
                {"r": [a.record_id, b.record_id, child.record_id]},
            ).all()
        )
        plain = {
            ref: keys.data_key(s, tenant_id=TENANT, key_ref=ref, create=False) for ref in wrapped
        }

    assert set(wrapped) == {a.record_id, b.record_id, child.record_id}  # issue 24: per record
    assert len({bytes(w) for w in wrapped.values()}) == 3
    assert len(set(plain.values())) == 3
    assert child.key_ref == child.record_id
    # The wrapped key only unwraps for its own tenant and record (KMS EncryptionContext).
    with allow_network(), pytest.raises(kms.exceptions.InvalidCiphertextException):
        kms.decrypt(
            CiphertextBlob=bytes(wrapped[a.record_id]),
            EncryptionContext={"tenant_id": TENANT, "key_ref": b.record_id},
        )


def test_the_tenant_key_lives_behind_its_alias(stack: Any) -> None:
    _, _, _, _, kms = stack

    with allow_network():
        meta = kms.describe_key(KeyId=tenant_alias(TENANT))["KeyMetadata"]

    assert meta["KeyState"] == "Enabled"
    assert meta["KeyUsage"] == "ENCRYPT_DECRYPT"


def test_record_key_is_the_only_ledger_table_the_app_may_delete_from(stack: Any) -> None:
    _, owner, _, _, _ = stack

    with owner.connect() as conn:
        deletable = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.role_table_grants "
                    "WHERE grantee = 'org_brain_app' AND privilege_type = 'DELETE'"
                )
            )
        }
        updatable = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.role_table_grants "
                    "WHERE grantee = 'org_brain_app' AND privilege_type = 'UPDATE'"
                )
            )
        }

    # search_doc is a rebuildable projection, not data; record_key is the only
    # ledger-side table rows are deleted from (crypto-shredding).
    assert deletable == {"record_key", "search_doc"}
    assert "record_key" not in updatable and "memory_record" not in updatable


def test_no_application_code_knows_about_the_emulator() -> None:
    offenders = [
        str(p.relative_to(APP))
        for p in APP.rglob("*.py")
        if re.search(r"9297|endpoint_url|aws-emulator|127\.0\.0\.1", p.read_text())
    ]

    assert offenders == []
