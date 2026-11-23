"""Evidence export (issue 23, doc 12 flow 5; Rev. C: COS → S3).

A served pack becomes an archived manifest an auditor can open years later:
- the pack as served (same content as `GET /context-packs/{id}`) and its
  sha256, with each body kept sealed under its record's own data key (so a
  record erased later is unreadable in the archive too; `open_pack` rebuilds
  the plaintext pack while the keys exist);
- for each `(record_id, version)` in it, that version's governance: status,
  who asserted it, who approved it (`approved_by`, with the decision id),
  authority, validity;
- the policy revision, and every verification of the pack so far.
The manifest is canonical JSON, so the same pack gives the same bytes. It is
encrypted under the **tenant's** KMS key (envelope: a fresh data key wrapped
by the tenant KEK, AES-256-GCM, tenant and pack as authenticated context), so
erasing the tenant (issue 24) makes every export unreadable too.

The object key is deterministic (`tenants/<tenant>/context-packs/<pack>.json`).
The worker checks it first: an object already there with the same manifest
hash is the same export, so retries never write a second locked version. The
object is put with Object Lock retention (mode and days by config).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.keys import tenant_alias
from app.models import EvidenceExport, MemoryRecord, Outbox, Verification
from app.services.access_gate import context_pack
from app.services.access_gate.context_pack import StoredPack
from app.services.body_codec import canonical
from app.utils.ids import new_record_id

EXPORT_REQUESTED = "evidence.export_requested"
MANIFEST_SCHEMA = "cortex.evidence/1"
ENVELOPE_FORMAT = "cortex.evidence.envelope/1"


def object_key(tenant_id: str, pack_id: str) -> str:
    return f"tenants/{tenant_id}/context-packs/{pack_id}.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_export(session: Session, pack_id: str) -> EvidenceExport | None:
    return session.scalars(select(EvidenceExport).where(EvidenceExport.pack_id == pack_id)).first()


def request_export(
    session: Session, *, stored: StoredPack, bucket: str
) -> tuple[EvidenceExport, bool]:
    """The export of this pack, created with its outbox event on first request.
    Returns (export, created)."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 23))"),
        {"k": f"{stored.pack.tenant_id}:{stored.pack.pack_id}"},
    )
    existing = find_export(session, stored.pack.pack_id)
    if existing is not None:
        return existing, False
    row = EvidenceExport(
        export_id=new_record_id(),
        tenant_id=stored.pack.tenant_id,
        pack_id=stored.pack.pack_id,
        bucket=bucket,
        object_key=object_key(stored.pack.tenant_id, stored.pack.pack_id),
    )
    session.add(row)
    session.flush()
    session.add(
        Outbox(
            tenant_id=row.tenant_id,
            event_type=EXPORT_REQUESTED,
            payload={"export_id": row.export_id, "pack_id": row.pack_id},
        )
    )
    session.flush()
    return row, True


def _iso(value: dt.datetime | dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def pack_view(stored: StoredPack) -> dict[str, Any]:
    return context_pack.render(stored).model_dump(mode="json")


def _sealed_view(stored: StoredPack, view: dict[str, Any]) -> dict[str, Any]:
    """The pack with each body replaced by its sealed snapshot (the record's
    own data key): erasing that record later makes the archived copy
    unreadable too, although the object itself is under retention (Q10)."""
    sealed = dict(view)
    sealed["records"] = [
        {
            **record,
            "body": None,
            "body_sealed": {
                "codec": item.body_codec,
                "key_ref": item.key_ref,
                "sealed": base64.b64encode(item.body_sealed).decode(),
            },
        }
        for record, (item, _) in zip(view["records"], stored.items, strict=True)
    ]
    return sealed


def open_pack(session: Session, manifest: dict[str, Any]) -> dict[str, Any]:
    """The pack as served, rebuilt from a manifest: each sealed body opened
    with its record's key. An erased record comes back with `body: null`."""
    from app.services.body_codec import BodyUnreadableError, codec_for

    records: list[dict[str, Any]] = []
    for record in manifest["pack"]["records"]:
        snap = record["body_sealed"]
        try:
            body: dict[str, Any] | None = codec_for(snap["codec"]).open(
                session,
                tenant_id=manifest["tenant_id"],
                key_ref=snap["key_ref"],
                sealed=base64.b64decode(snap["sealed"]),
            )
        except BodyUnreadableError:
            body = None
        rest = {k: v for k, v in record.items() if k != "body_sealed"}
        records.append({**rest, "body": body, "body_erased": body is None})
    return {**manifest["pack"], "records": records}


def build_manifest(session: Session, stored: StoredPack, pack_view: dict[str, Any]) -> bytes:
    """Canonical JSON bytes. `pack_view` is the pack as the API serves it."""
    records: list[dict[str, Any]] = []
    for item, _ in stored.items:
        version = session.scalars(
            select(MemoryRecord).where(
                MemoryRecord.record_id == item.record_id, MemoryRecord.version == item.version
            )
        ).one()
        records.append(
            {
                "record_id": item.record_id,
                "version": item.version,
                "role": item.role,
                "body_hash": item.body_hash,
                "status": version.status,
                "asserted_by": version.asserted_by,
                "approved_by": version.approved_by,
                "authority": version.authority,
                "enforcement": version.enforcement,
                "valid_from": _iso(version.valid_from),
                "valid_to": _iso(version.valid_to),
                "recorded_at": _iso(version.recorded_at),
            }
        )
    verifications = [
        {
            "verification_id": v.verification_id,
            "result": v.result,
            "reasons": v.reasons,
            "output_hash": v.output_hash,
            "policy_revision": v.policy_revision,
            "verified_at": _iso(v.verified_at),
        }
        for v in session.scalars(
            select(Verification)
            .where(Verification.pack_id == stored.pack.pack_id)
            .order_by(Verification.id)
        )
    ]
    pack_bytes = canonical(pack_view)
    return canonical(
        {
            "schema": MANIFEST_SCHEMA,
            "tenant_id": stored.pack.tenant_id,
            "pack_id": stored.pack.pack_id,
            "pack_sha256": sha256(pack_bytes),  # of the pack as served, bodies in clear
            "pack": _sealed_view(stored, pack_view),
            "policy_revision": stored.pack.policy_revision,
            "case_hash": stored.pack.case_hash,
            "records": records,
            "verifications": verifications,
        }
    )


@dataclass(frozen=True)
class Exported:
    version_id: str | None
    manifest_sha256: str
    retain_until: dt.datetime | None
    reused: bool


class EvidenceArchive:
    """S3 + KMS. boto3 clients come configured by the environment only."""

    def __init__(self, s3: Any, kms: Any, *, retention_mode: str, retention_days: int) -> None:
        self._s3 = s3
        self._kms = kms
        self.retention_mode = retention_mode
        self.retention_days = retention_days

    @staticmethod
    def _context(tenant_id: str, pack_id: str) -> dict[str, str]:
        return {"tenant_id": tenant_id, "pack_id": pack_id, "purpose": "evidence"}

    def seal(self, manifest: bytes, *, tenant_id: str, pack_id: str) -> bytes:
        context = self._context(tenant_id, pack_id)
        key = self._kms.generate_data_key(
            KeyId=tenant_alias(tenant_id), KeySpec="AES_256", EncryptionContext=context
        )
        nonce = os.urandom(12)
        aad = canonical(context)
        ciphertext = AESGCM(bytes(key["Plaintext"])).encrypt(nonce, manifest, aad)
        envelope = {
            "format": ENVELOPE_FORMAT,
            "kek_alias": tenant_alias(tenant_id),
            "context": context,
            "wrapped_key": base64.b64encode(bytes(key["CiphertextBlob"])).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }
        return json.dumps(envelope, sort_keys=True).encode()

    def open(self, envelope_bytes: bytes) -> bytes:
        envelope = json.loads(envelope_bytes)
        if envelope.get("format") != ENVELOPE_FORMAT:
            raise ValueError("not a Cortex evidence envelope")
        context = envelope["context"]
        key = self._kms.decrypt(
            CiphertextBlob=base64.b64decode(envelope["wrapped_key"]), EncryptionContext=context
        )
        return AESGCM(bytes(key["Plaintext"])).decrypt(
            base64.b64decode(envelope["nonce"]),
            base64.b64decode(envelope["ciphertext"]),
            canonical(context),
        )

    def put(self, export: EvidenceExport, manifest: bytes) -> Exported:
        digest = sha256(manifest)
        try:
            head = self._s3.head_object(Bucket=export.bucket, Key=export.object_key)
        except self._s3.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                raise
            head = None
        if head is not None:
            if head.get("Metadata", {}).get("manifest-sha256") != digest:
                raise RuntimeError(
                    f"{export.object_key} already holds a different manifest: refusing to overwrite"
                )
            return Exported(
                head.get("VersionId"), digest, head.get("ObjectLockRetainUntilDate"), reused=True
            )
        retain_until = dt.datetime.now(dt.UTC) + dt.timedelta(days=self.retention_days)
        put = self._s3.put_object(
            Bucket=export.bucket,
            Key=export.object_key,
            Body=self.seal(manifest, tenant_id=export.tenant_id, pack_id=export.pack_id),
            ContentType="application/json",
            Metadata={"manifest-sha256": digest, "schema": MANIFEST_SCHEMA},
            ObjectLockMode=self.retention_mode,
            ObjectLockRetainUntilDate=retain_until,
        )
        return Exported(put.get("VersionId"), digest, retain_until, reused=False)


_archive: list[EvidenceArchive] = []


def install_archive(archive: EvidenceArchive) -> None:
    _archive[:] = [archive]


def archive() -> EvidenceArchive:
    if not _archive:
        import boto3  # pyright: ignore[reportMissingTypeStubs]

        from app.config import OrgBrainConfig

        cfg = OrgBrainConfig()
        install_archive(
            EvidenceArchive(
                boto3.client("s3"),  # pyright: ignore[reportUnknownMemberType]
                boto3.client("kms"),  # pyright: ignore[reportUnknownMemberType]
                retention_mode=cfg.evidence_retention_mode,
                retention_days=cfg.evidence_retention_days,
            )
        )
    return _archive[0]


def run_export(session: Session, export_id: str) -> EvidenceExport | None:
    """The worker's job: build, seal, put, record. Idempotent."""
    row = session.scalars(
        select(EvidenceExport).where(EvidenceExport.export_id == export_id)
    ).first()
    if row is None or row.status == "exported":
        return None
    stored = context_pack.load(session, row.pack_id)
    manifest = build_manifest(session, stored, pack_view(stored))
    result = archive().put(row, manifest)
    row.status = "exported"
    row.manifest_sha256 = result.manifest_sha256
    row.version_id = result.version_id
    row.retention_mode = archive().retention_mode
    row.retain_until = result.retain_until
    row.exported_at = dt.datetime.now(dt.UTC)
    session.flush()
    return row
