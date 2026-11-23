"""Erasure by crypto-shredding (issue 24, Q10; APPI "erasure by lineage").

The ledger is append-only, so nothing is deleted from it. Instead the keys
that open the bodies are destroyed:

- **A record** (`erase_record`): the record and every record derived from it
  (following `derived_from` in any position, transitively) get an `erased`
  version; their data keys (`record_key` rows) are deleted; their search
  rows are deleted. Every copy sealed under those keys becomes unreadable at
  once: the ledger bodies, the served-pack snapshots and the evidence
  manifests' per-record bodies. Packs and manifests stay whole (ids, versions,
  roles, body hashes); only those bodies are gone. A key still shared with a
  record outside that set (rows sealed before issue 24 used the lineage
  root's key) widens the erasure to that record too, and the report says so.
- **A tenant** (`erase_tenant`): its KMS key is disabled and scheduled for
  deletion, every wrapped data key and cached plaintext key is dropped and its
  search projection is deleted, so no body, pack or evidence export of the
  tenant opens any more.

Each erasure is written, with its report, to the append-only `erasure` table.
The report holds ids and counts, never body text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.keys import installed, tenant_alias
from app.models import Erasure, EvidenceExport, MemoryRecord, ServedPackItem, Status
from app.services import ledger
from app.utils.ids import new_record_id

KEY_DELETION_WINDOW_DAYS = 7  # the KMS minimum

_DESCENDANTS = text(
    """
    WITH RECURSIVE erased(record_id) AS (
        SELECT CAST(:root AS uuid)
        UNION
        SELECT m.record_id FROM memory_record_current m
        JOIN erased e ON e.record_id = ANY(m.derived_from)
    )
    SELECT record_id::text FROM erased
    """
)


class NothingToEraseError(LookupError):
    pass


@dataclass
class Report:
    scope: str
    subject: str
    records: list[str] = field(default_factory=list[str])
    collateral: list[str] = field(default_factory=list[str])
    keys_shredded: list[str] = field(default_factory=list[str])
    projection_rows_deleted: int = 0
    packs_affected: list[str] = field(default_factory=list[str])
    exports_affected: list[str] = field(default_factory=list[str])
    kms: dict[str, Any] = field(default_factory=dict[str, Any])

    def as_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k not in ("scope", "subject")}


def _closure(session: Session, record_id: str) -> set[str]:
    return set(session.execute(_DESCENDANTS, {"root": record_id}).scalars())


def _key_refs(session: Session, record_ids: set[str]) -> set[str]:
    rows = session.execute(
        select(MemoryRecord.record_id, MemoryRecord.key_ref).where(
            MemoryRecord.record_id.in_(record_ids)
        )
    ).all()
    return {str(r.key_ref or r.record_id) for r in rows}


def _sharing(session: Session, key_refs: set[str]) -> set[str]:
    rows = session.execute(
        select(MemoryRecord.record_id).where(MemoryRecord.key_ref.in_(key_refs)).distinct()
    ).scalars()
    return {str(r) for r in rows}


def _affected(session: Session, key_refs: set[str], report: Report) -> None:
    packs = sorted(
        {
            str(p)
            for p in session.execute(
                select(ServedPackItem.pack_id).where(ServedPackItem.key_ref.in_(key_refs))
            ).scalars()
        }
    )
    report.packs_affected = packs
    report.exports_affected = sorted(
        str(e)
        for e in session.execute(
            select(EvidenceExport.export_id).where(EvidenceExport.pack_id.in_(packs))
        ).scalars()
    )


def _log(
    session: Session, *, tenant_id: str, report: Report, requested_by: str, reason: str
) -> str:
    erasure_id = new_record_id()
    session.add(
        Erasure(
            erasure_id=erasure_id,
            tenant_id=tenant_id,
            scope=report.scope,
            subject=report.subject,
            requested_by=requested_by,
            reason=reason,
            report=report.as_json(),
        )
    )
    session.flush()
    return erasure_id


def erase_record(
    session: Session, *, tenant_id: str, record_id: str, requested_by: str, reason: str
) -> tuple[str, Report]:
    """Erase a record and everything derived from it. Idempotent: already
    erased records are reported, not erased twice."""
    try:
        ledger.latest(session, record_id)
    except ledger.RecordNotFoundError:
        raise NothingToEraseError(record_id) from None
    report = Report("record", record_id)
    erased = _closure(session, record_id)
    keys = _key_refs(session, erased)
    while True:  # a shared (pre-issue-24) key widens the set; follow it to a fixpoint
        widened = _sharing(session, keys) | erased
        widened |= {d for r in widened - erased for d in _closure(session, r)}
        if widened == erased:
            break
        report.collateral = sorted(set(report.collateral) | (widened - erased))
        erased = widened
        keys = _key_refs(session, erased)
    for rid in sorted(erased):
        if ledger.latest(session, rid).status != Status.ERASED.value:
            ledger.transition(session, record_id=rid, to=Status.ERASED)
    report.records = sorted(erased)
    report.keys_shredded = sorted(
        str(k)
        for k in session.execute(
            text(
                "DELETE FROM record_key WHERE key_ref = ANY(CAST(:k AS uuid[])) "
                "RETURNING key_ref::text"
            ),
            {"k": sorted(keys)},
        ).scalars()
    )
    report.projection_rows_deleted = len(
        session.execute(
            text(
                "DELETE FROM search_doc WHERE record_id = ANY(CAST(:r AS uuid[])) "
                "RETURNING record_id"
            ),
            {"r": sorted(erased)},
        ).all()
    )
    _affected(session, keys, report)
    service = installed()
    if service is not None:
        service.forget_keys(tenant_id, keys)
    erasure_id = _log(
        session, tenant_id=tenant_id, report=report, requested_by=requested_by, reason=reason
    )
    return erasure_id, report


def erase_tenant(
    session: Session, *, tenant_id: str, kms: Any, requested_by: str, reason: str
) -> tuple[str, Report]:
    """Crypto-shred a whole tenant: its KEK is disabled and scheduled for
    deletion, its wrapped data keys, cached keys and search projection go."""
    report = Report("tenant", tenant_id)
    key_id = str(kms.describe_key(KeyId=tenant_alias(tenant_id))["KeyMetadata"]["KeyId"])
    kms.disable_key(KeyId=key_id)
    deletion = kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=KEY_DELETION_WINDOW_DAYS)
    state = kms.describe_key(KeyId=key_id)["KeyMetadata"]["KeyState"]
    report.kms = {
        "key_id": key_id,
        "alias": tenant_alias(tenant_id),
        "disabled": True,
        "deletion_date": str(deletion.get("DeletionDate")),
        "key_state": state,
    }
    report.keys_shredded = sorted(
        str(k)
        for k in session.execute(
            text("DELETE FROM record_key WHERE tenant_id = :t RETURNING key_ref::text"),
            {"t": tenant_id},
        ).scalars()
    )
    report.projection_rows_deleted = len(
        session.execute(
            text("DELETE FROM search_doc WHERE tenant_id = :t RETURNING record_id"),
            {"t": tenant_id},
        ).all()
    )
    report.packs_affected = sorted(
        str(p) for p in session.execute(text("SELECT pack_id::text FROM served_pack")).scalars()
    )
    report.exports_affected = sorted(
        str(e)
        for e in session.execute(text("SELECT export_id::text FROM evidence_export")).scalars()
    )
    service = installed()
    if service is not None:
        service.forget(tenant_id)
    erasure_id = _log(
        session, tenant_id=tenant_id, report=report, requested_by=requested_by, reason=reason
    )
    return erasure_id, report
