"""Tenant keys (KMS) and the AES-GCM body codec. `install()` is what every
process that writes or reads sealed bodies calls at startup."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

import boto3  # pyright: ignore[reportMissingTypeStubs]

from app.keys.kms import AesGcmCodec, KeyErasedError, KeyService, KmsKeyService, tenant_alias
from app.services.body_codec import install_codec


def install(tenant_ids: Iterable[str] = (), kms: Any | None = None) -> KmsKeyService:
    """Build the KMS key service, make sure each tenant has its KEK, and make
    `aes-gcm/1` the codec for new bodies in this process."""
    client: Any = kms if kms is not None else cast(Any, boto3).client("kms")
    keys = KmsKeyService(client)
    for tenant_id in tenant_ids:
        keys.ensure_tenant_key(tenant_id)
    install_codec(AesGcmCodec(keys), default=True)
    return keys


__all__ = [
    "AesGcmCodec",
    "KeyErasedError",
    "KeyService",
    "KmsKeyService",
    "install",
    "tenant_alias",
]
