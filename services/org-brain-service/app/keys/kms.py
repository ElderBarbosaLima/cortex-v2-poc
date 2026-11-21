"""Tenant keys and record data keys (Rev. C "behind one key interface"; Q10).

- One KMS key per tenant, the KEK, reached through `alias/cortex/tenant/<id>`
  and created at tenant onboarding (no tags: KMS keys stay out of Terraform).
- One data key (DEK) per lineage root, from `GenerateDataKey` with an
  EncryptionContext naming the tenant and the root, so a wrapped DEK unwraps
  only for that tenant and that record (the IAM policy also denies KMS use
  without a tenant_id context). The wrapped DEK lives in `record_key`; the
  plaintext DEK only in this process's memory.
- Erasing a record = deleting its `record_key` row (issue 24); erasing a tenant
  = ScheduleKeyDeletion on its KEK. Either way the body can no longer be opened.

AWS is reached through boto3 configured only by environment variables.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RecordKey
from app.services.body_codec import Body, BodyUnreadableError, canonical

_FORMAT = b"\x01"
_NONCE = 12


class KeyErasedError(BodyUnreadableError):
    """The record's data key was deleted: the body is crypto-shredded."""


def tenant_alias(tenant_id: str) -> str:
    return f"alias/cortex/tenant/{tenant_id}"


class KeyService(Protocol):
    def ensure_tenant_key(self, tenant_id: str) -> str: ...

    def data_key(
        self, session: Session, *, tenant_id: str, key_ref: str, create: bool
    ) -> bytes: ...

    def forget(self, tenant_id: str | None = None) -> None: ...


class KmsKeyService:
    def __init__(self, kms: Any) -> None:
        self._kms = kms
        self._cache: dict[tuple[str, str], bytes] = {}

    def ensure_tenant_key(self, tenant_id: str) -> str:
        alias = tenant_alias(tenant_id)
        try:
            return str(self._kms.describe_key(KeyId=alias)["KeyMetadata"]["KeyId"])
        except self._kms.exceptions.NotFoundException:
            key_id = str(
                self._kms.create_key(
                    Description=f"Cortex tenant {tenant_id}",
                    KeyUsage="ENCRYPT_DECRYPT",
                    KeySpec="SYMMETRIC_DEFAULT",
                )["KeyMetadata"]["KeyId"]
            )
            self._kms.create_alias(AliasName=alias, TargetKeyId=key_id)
            return key_id

    def data_key(self, session: Session, *, tenant_id: str, key_ref: str, create: bool) -> bytes:
        cached = self._cache.get((tenant_id, key_ref))
        if cached is not None:
            return cached
        context = {"tenant_id": tenant_id, "key_ref": key_ref}
        row = session.scalars(
            select(RecordKey).where(RecordKey.tenant_id == tenant_id, RecordKey.key_ref == key_ref)
        ).first()
        if row is not None:
            plaintext = bytes(
                self._kms.decrypt(CiphertextBlob=row.wrapped_dek, EncryptionContext=context)[
                    "Plaintext"
                ]
            )
        elif create:
            generated = self._kms.generate_data_key(
                KeyId=tenant_alias(tenant_id), KeySpec="AES_256", EncryptionContext=context
            )
            plaintext = bytes(generated["Plaintext"])
            session.add(
                RecordKey(
                    tenant_id=tenant_id,
                    key_ref=key_ref,
                    wrapped_dek=bytes(generated["CiphertextBlob"]),
                    kek_alias=tenant_alias(tenant_id),
                )
            )
            session.flush()
        else:
            raise KeyErasedError(f"no data key for {key_ref}: erased")
        self._cache[(tenant_id, key_ref)] = plaintext
        return plaintext

    def forget(self, tenant_id: str | None = None) -> None:
        """Drop cached plaintext keys (all, or one tenant's) — part of erasure."""
        for cache_key in [k for k in self._cache if tenant_id is None or k[0] == tenant_id]:
            del self._cache[cache_key]


class AesGcmCodec:
    """AES-256-GCM with the lineage root's data key. The tenant and the root are
    authenticated data, so a body moved to another tenant or record fails."""

    name = "aes-gcm/1"

    def __init__(self, keys: KeyService) -> None:
        self._keys = keys

    @staticmethod
    def _aad(tenant_id: str, key_ref: str) -> bytes:
        return f"cortex:{tenant_id}:{key_ref}".encode()

    def seal(self, session: Session, *, tenant_id: str, key_ref: str, body: Body) -> bytes:
        dek = self._keys.data_key(session, tenant_id=tenant_id, key_ref=key_ref, create=True)
        nonce = os.urandom(_NONCE)
        return (
            _FORMAT
            + nonce
            + AESGCM(dek).encrypt(nonce, canonical(body), self._aad(tenant_id, key_ref))
        )

    def open(self, session: Session, *, tenant_id: str, key_ref: str, sealed: bytes) -> Body:
        if not sealed.startswith(_FORMAT):
            raise BodyUnreadableError("unknown sealed-body format")
        dek = self._keys.data_key(session, tenant_id=tenant_id, key_ref=key_ref, create=False)
        nonce, ciphertext = sealed[1 : 1 + _NONCE], sealed[1 + _NONCE :]
        try:
            plaintext = AESGCM(dek).decrypt(nonce, ciphertext, self._aad(tenant_id, key_ref))
        except InvalidTag:
            raise BodyUnreadableError(
                "body failed authentication (tampered, or another tenant's)"
            ) from None
        loaded: Body = json.loads(plaintext)
        return loaded
