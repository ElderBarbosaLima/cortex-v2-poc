"""Regions, option (b) of ADR 0002 (issue 32): each region runs its own Cortex,
and a published Global scope is copied into the others.

A record crosses regions only when, in its home region, it is:
- **in the published scope** (the PoC publishes the tenant root);
- **approved** (proposals, rejections and drafts never leave);
- **not personal**: `sensitivity.personal_data` is false and its class is in
  `PUBLISHABLE_CLASSES`. A personal record never leaves its region (APPI).

The replica keeps the `record_id`, so packs, lineage and erasure name the same
record everywhere. It is **read-only** in the destination: `asserted_by` is
`{type: replication, id: region:<home>, origin: <the home asserter>}`,
`source.replica_of` names the home version and body hash, and only this job
changes it. The body is re-sealed under the destination's own data key (a new
`key_ref`), never copied as ciphertext.

The job is idempotent: a second run with nothing new changes nothing. It
carries every later status of a replicated record across: stale, deprecated
(retired), superseded, and erased (crypto-shredded in the destination too,
with everything derived from it there). A record that stops being publishable
is erased from the other regions.

Between two runs the destination lags. Verify in the destination only sees the
retirement once it is replicated: that lag is the cost of option (b).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.models import Kind, MemoryRecord, Status
from app.services import erasure, ledger
from app.utils.ids import new_record_id

PUBLISHABLE_CLASSES = frozenset({"public", "internal"})
REPLICATOR = "replication"


@dataclass
class Report:
    region: str
    published: list[str] = field(default_factory=list[str])
    updated: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    erased: list[str] = field(default_factory=list[str])
    withheld: list[str] = field(default_factory=list[str])  # personal or above the ceiling
    unchanged: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "published": self.published,
            "updated": [list(u) for u in self.updated],
            "erased": self.erased,
            "withheld": self.withheld,
            "unchanged": self.unchanged,
        }


def publishable(row: MemoryRecord) -> bool:
    sensitivity: dict[str, Any] = row.sensitivity or {}
    return (
        not sensitivity.get("personal_data", False) and row.sensitivity_class in PUBLISHABLE_CLASSES
    )


def is_replica(row: MemoryRecord) -> bool:
    return (row.asserted_by or {}).get("type") == REPLICATOR


def _latest_in_scope(session: Session, tenant_id: str, scope: str) -> list[MemoryRecord]:
    """The latest version of every record in `scope`, oldest record first."""
    latest = (
        select(MemoryRecord)
        .where(MemoryRecord.tenant_id == tenant_id, MemoryRecord.scope == scope)
        .order_by(MemoryRecord.record_id, MemoryRecord.version.desc())
        .ext(distinct_on(MemoryRecord.record_id))
    )
    rows = list(session.scalars(latest))
    return sorted(rows, key=lambda r: r.record_id)  # ULID-based ids sort by creation


def _find(session: Session, record_id: str) -> MemoryRecord | None:
    try:
        return ledger.latest(session, record_id)
    except ledger.RecordNotFoundError:
        return None


def _replica_draft(src: Session, row: MemoryRecord, region: str, scope: str) -> ledger.RecordDraft:
    return ledger.RecordDraft(
        kind=Kind(row.kind),
        schema_version=row.schema_version,
        body=ledger.open_body(src, row),
        scope=scope,
        asserted_by={"type": REPLICATOR, "id": f"region:{region}", "origin": row.asserted_by},
        authority=row.authority,
        source={
            **(row.source or {}),
            "replica_of": {
                "region": region,
                "record_id": row.record_id,
                "version": row.version,
                "body_hash": row.body_hash,
            },
        },
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        enforcement=row.enforcement,
        entities=tuple(row.entities or ()),
        assurance_level=row.assurance_level,
        derived_from=tuple(row.derived_from or ()),
        sensitivity=dict(row.sensitivity or {}),
    )


def replicate(
    src: Session,
    dst: Session,
    *,
    tenant_id: str,
    region: str,
    src_scope: str,
    dst_scope: str,
) -> Report:
    """Copy `region`'s published records into the destination ledger, and carry
    every later change of the ones already copied. Both sessions are bound to
    `tenant_id` (RLS on both sides)."""
    report = Report(region)
    for home in _latest_in_scope(src, tenant_id, src_scope):
        if is_replica(home):
            continue  # never re-export a copy: a record has one home region
        here = _find(dst, home.record_id)
        if here is not None and not is_replica(here):
            raise ValueError(f"{home.record_id} exists in the destination and is not a replica")
        gone = home.status == Status.ERASED.value or not publishable(home)
        if gone:
            if here is not None and here.status != Status.ERASED.value:
                erasure.erase_record(
                    dst,
                    tenant_id=tenant_id,
                    record_id=home.record_id,
                    requested_by=f"region:{region}",
                    reason="erased or withdrawn in its home region",
                )
                report.erased.append(home.record_id)
            elif home.status != Status.ERASED.value:
                report.withheld.append(home.record_id)
            else:
                report.unchanged += 1
            continue
        if here is None:
            if home.status != Status.APPROVED.value:
                report.unchanged += 1  # never published while approved: nothing to copy
                continue
            ledger.append_new(
                dst,
                tenant_id=tenant_id,
                draft=_replica_draft(src, home, region, dst_scope),
                status=Status.APPROVED,
                approved_by=home.approved_by,
                supersedes=home.supersedes,
                record_id=home.record_id,
                key_ref=new_record_id(),
            )
            report.published.append(home.record_id)
            continue
        if here.status == home.status:
            report.unchanged += 1
            continue
        to = Status(home.status)
        if to is Status.PROPOSED:
            report.unchanged += 1
            continue
        ledger.transition(
            dst,
            record_id=home.record_id,
            to=to,
            approved_by=home.approved_by if to is Status.APPROVED else None,
        )
        report.updated.append((home.record_id, to.value))
    return report
