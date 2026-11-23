"""Tenant KEKs, per-lineage DEKs and the AES-GCM codec, with an in-memory KMS
(hermetic). The real KMS path is tests/integration/test_record_keys_db.py."""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.keys import AesGcmCodec, KeyErasedError, KmsKeyService
from app.models import Kind, MemoryRecord, RecordKey
from app.services import ledger
from app.services.body_codec import BodyUnreadableError
from tests.factories import SCOPE, TENANT_A, TENANT_B


class FakeKms:
    """Just enough of KMS: aliases, data keys bound to an EncryptionContext."""

    class exceptions:
        class NotFoundException(Exception):
            pass

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.calls: list[str] = []

    def describe_key(self, KeyId: str) -> dict[str, Any]:
        self.calls.append("describe_key")
        if KeyId not in self.aliases:
            raise self.exceptions.NotFoundException(KeyId)
        return {"KeyMetadata": {"KeyId": self.aliases[KeyId]}}

    def create_key(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("create_key")
        return {"KeyMetadata": {"KeyId": f"key-{len(self.calls)}"}}

    def create_alias(self, AliasName: str, TargetKeyId: str) -> None:
        self.aliases[AliasName] = TargetKeyId

    def generate_data_key(
        self, KeyId: str, KeySpec: str, EncryptionContext: dict[str, str]
    ) -> dict[str, Any]:
        plaintext = os.urandom(32)
        blob = json.dumps({"key": KeyId, "ctx": EncryptionContext, "dek": plaintext.hex()}).encode()
        return {"Plaintext": plaintext, "CiphertextBlob": blob}

    def decrypt(self, CiphertextBlob: bytes, EncryptionContext: dict[str, str]) -> dict[str, Any]:
        blob = json.loads(CiphertextBlob)
        if blob["ctx"] != EncryptionContext:
            raise ValueError("InvalidCiphertextException")
        return {"Plaintext": bytes.fromhex(blob["dek"])}


def draft(**overrides: Any) -> ledger.RecordDraft:
    fields: dict[str, Any] = {
        "kind": Kind.RULE,
        "schema_version": "test.rule/1",
        "body": {"text_ja": "注文番号を備考に記載する"},
        "scope": SCOPE,
        "asserted_by": {"type": "user", "id": "R-001"},
        "authority": 10,
        "source": {"system": "test"},
        "valid_from": dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        "enforcement": "mandatory",
    }
    fields.update(overrides)
    return ledger.RecordDraft(**fields)


@pytest.fixture
def keys() -> KmsKeyService:
    return KmsKeyService(FakeKms())


def test_a_tenant_key_is_created_once_then_reused() -> None:
    kms = FakeKms()
    keys = KmsKeyService(kms)

    first = keys.ensure_tenant_key(TENANT_A)
    again = keys.ensure_tenant_key(TENANT_A)

    assert first == again
    assert kms.calls.count("create_key") == 1
    assert f"alias/cortex/tenant/{TENANT_A}" in kms.aliases


def test_bodies_are_sealed_and_open_back(sqlite_session: Session, keys: KmsKeyService) -> None:
    codec = AesGcmCodec(keys)
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)

    assert row.body_codec == "aes-gcm/1"
    assert "注文番号".encode() not in row.body_sealed
    assert ledger.open_body(sqlite_session, row, codec) == draft().body
    assert row.key_ref == row.record_id  # its own lineage root


def test_every_record_gets_its_own_data_key_derived_ones_too(
    sqlite_session: Session, keys: KmsKeyService
) -> None:
    codec = AesGcmCodec(keys)
    a = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)
    b = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)
    child = ledger.append_new(
        sqlite_session, tenant_id=TENANT_A, draft=draft(derived_from=(a.record_id,)), codec=codec
    )
    grandchild = ledger.append_new(
        sqlite_session,
        tenant_id=TENANT_A,
        draft=draft(derived_from=(child.record_id,)),
        codec=codec,
    )
    wrapped = {k.key_ref: k.wrapped_dek for k in sqlite_session.scalars(select(RecordKey))}

    # Issue 24: a key per record (Q10), so a derived record can be erased on
    # its own; erasing a parent follows derived_from (app/services/erasure.py).
    ids = {a.record_id, b.record_id, child.record_id, grandchild.record_id}
    assert set(wrapped) == ids
    assert len(set(wrapped.values())) == 4
    assert all(r.key_ref == r.record_id for r in (a, b, child, grandchild))
    assert ledger.open_body(sqlite_session, grandchild, codec) == draft().body


def test_a_new_version_keeps_the_key_reference(
    sqlite_session: Session, keys: KmsKeyService
) -> None:
    codec = AesGcmCodec(keys)
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)

    v2 = ledger.transition(sqlite_session, record_id=row.record_id, to=ledger.Status.DEPRECATED)

    assert v2.key_ref == row.key_ref
    assert ledger.open_body(sqlite_session, v2, codec) == draft().body


def test_a_body_moved_to_another_tenant_or_tampered_does_not_open(
    sqlite_session: Session, keys: KmsKeyService
) -> None:
    codec = AesGcmCodec(keys)
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)
    flipped = row.body_sealed[:-1] + bytes([row.body_sealed[-1] ^ 1])
    keys.data_key(sqlite_session, tenant_id=TENANT_B, key_ref=row.record_id, create=True)

    with pytest.raises(BodyUnreadableError, match="authentication"):
        codec.open(sqlite_session, tenant_id=TENANT_A, key_ref=row.record_id, sealed=flipped)
    with pytest.raises(BodyUnreadableError, match="authentication"):
        codec.open(
            sqlite_session, tenant_id=TENANT_B, key_ref=row.record_id, sealed=row.body_sealed
        )


def test_deleting_the_data_key_shreds_the_body(
    sqlite_session: Session, keys: KmsKeyService
) -> None:
    codec = AesGcmCodec(keys)
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)

    sqlite_session.query(RecordKey).filter(RecordKey.key_ref == row.record_id).delete()
    keys.forget(TENANT_A)

    with pytest.raises(KeyErasedError):
        ledger.open_body(sqlite_session, sqlite_session.get(MemoryRecord, row.id) or row, codec)


def test_a_warm_cache_does_not_outlive_an_erasure_done_elsewhere(
    sqlite_session: Session, keys: KmsKeyService
) -> None:
    codec = AesGcmCodec(keys)
    row = ledger.append_new(sqlite_session, tenant_id=TENANT_A, draft=draft(), codec=codec)
    assert ledger.open_body(sqlite_session, row, codec) == draft().body  # key now cached

    # Another process erases the record: its wrapped key row disappears.
    for key in sqlite_session.scalars(select(RecordKey).where(RecordKey.key_ref == row.record_id)):
        sqlite_session.delete(key)
    sqlite_session.flush()

    with pytest.raises(KeyErasedError):
        ledger.open_body(sqlite_session, row, codec)
