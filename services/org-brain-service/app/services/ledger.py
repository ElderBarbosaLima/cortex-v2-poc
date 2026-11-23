"""Ledger commands: append, transition, supersede, and reads "as of T".

Same contract as the monorepo's `knowledge_commands`: each command writes its
ledger row(s) and exactly one outbox row per row in the caller's transaction,
and flushes but never commits, so a command and its side effects land
atomically or not at all. Nothing is updated in place: a status change appends
the next version of the record (Rev. B rule 3, "supersede, don't delete").

Two clocks (bitemporal): `recorded_at` is system time (when the ledger learned
it, set by the database); `valid_from`/`valid_to` is business time (when the
fact or rule holds in the world).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Kind, MemoryRecord, Outbox, Status
from app.services.body_codec import Body, BodyCodec, body_hash, codec_for, default_codec
from app.utils.ids import new_record_id

# A model may never assert above "preference", at any confidence (governance v1
# MODEL_ACTOR_CEILING). Only an approval by a person lifts a record above it.
MODEL_AUTHORITY_CEILING = 10
# Actors that are not a person: a model (an agent asserting alone) or a policy
# (a rule suggested from corrections, D17). Both stay under the ceiling.
MACHINE_ACTORS = frozenset({"model", "policy"})

RECORD_APPENDED = "record.appended"

# status -> statuses it may move to. erased is terminal; so is superseded.
_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.PROPOSED: frozenset(
        {Status.APPROVED, Status.DEPRECATED, Status.SUPERSEDED, Status.ERASED}
    ),
    Status.APPROVED: frozenset({Status.DEPRECATED, Status.STALE, Status.SUPERSEDED, Status.ERASED}),
    Status.STALE: frozenset({Status.APPROVED, Status.DEPRECATED, Status.SUPERSEDED, Status.ERASED}),
    Status.DEPRECATED: frozenset({Status.ERASED}),
    Status.SUPERSEDED: frozenset({Status.ERASED}),
    Status.ERASED: frozenset(),
}
TERMINAL_STATUSES = frozenset(s for s, nxt in _TRANSITIONS.items() if not nxt)


class IllegalTransitionError(ValueError):
    """A command was issued against a status that does not allow it."""


class ModelCeilingError(ValueError):
    """A model actor tried to hold a record above the model authority ceiling."""


class RecordNotFoundError(LookupError):
    """No visible record with that id (absent, or another tenant's under RLS)."""


@dataclass(frozen=True)
class RecordDraft:
    kind: Kind
    schema_version: str
    body: Body
    scope: str
    asserted_by: dict[str, Any]
    authority: int
    source: dict[str, Any]
    valid_from: dt.datetime
    enforcement: str | None = None
    entities: tuple[str, ...] = ()
    assurance_level: str = "unverified"
    valid_to: dt.datetime | None = None
    derived_from: tuple[str, ...] = ()
    sensitivity: dict[str, Any] = field(
        default_factory=lambda: {"class": "internal", "personal_data": False}
    )


def _check_ceiling(
    asserted_by: dict[str, Any], authority: int, approved_by: dict[str, Any] | None
) -> None:
    approved_by_person = approved_by is not None and approved_by.get("approver_type") == "user"
    if (
        asserted_by.get("type") in MACHINE_ACTORS
        and authority > MODEL_AUTHORITY_CEILING
        and not approved_by_person
    ):
        raise ModelCeilingError(
            f"a {asserted_by.get('type')} actor cannot hold authority {authority}; the ceiling is "
            f"{MODEL_AUTHORITY_CEILING} until a person approves"
        )


def _emit(session: Session, row: MemoryRecord) -> None:
    session.add(
        Outbox(
            tenant_id=row.tenant_id,
            event_type=RECORD_APPENDED,
            payload={
                "record_id": row.record_id,
                "version": row.version,
                "status": row.status,
                "kind": row.kind,
            },
            subject_record_id=row.record_id,
        )
    )


def append_new(
    session: Session,
    *,
    tenant_id: str,
    draft: RecordDraft,
    status: Status = Status.PROPOSED,
    approved_by: dict[str, Any] | None = None,
    supersedes: str | None = None,
    codec: BodyCodec | None = None,
    record_id: str | None = None,
    key_ref: str | None = None,
) -> MemoryRecord:
    """Version 1 of a new record. `record_id` is minted here unless the caller
    needs to know it first (a decision record is named by its own decision id)."""
    if status not in (Status.PROPOSED, Status.APPROVED):
        raise IllegalTransitionError(f"a new record starts proposed or approved, not {status}")
    if status is Status.APPROVED and approved_by is None:
        raise IllegalTransitionError("an approved record needs approved_by")
    _check_ceiling(draft.asserted_by, draft.authority, approved_by)
    codec = codec or default_codec()
    record_id = record_id or new_record_id()
    key_ref = key_ref or _record_key_ref(record_id)  # a replica brings its own (issue 32)
    row = MemoryRecord(
        record_id=record_id,
        version=1,
        tenant_id=tenant_id,
        kind=draft.kind.value,
        schema_version=draft.schema_version,
        body_sealed=codec.seal(session, tenant_id=tenant_id, key_ref=key_ref, body=draft.body),
        body_codec=codec.name,
        body_hash=body_hash(draft.body),
        key_ref=key_ref,
        entities=list(draft.entities),
        scope=draft.scope,
        source=draft.source,
        asserted_by=draft.asserted_by,
        approved_by=approved_by,
        status=status.value,
        enforcement=draft.enforcement,
        authority=draft.authority,
        assurance_level=draft.assurance_level,
        valid_from=draft.valid_from,
        valid_to=draft.valid_to,
        supersedes=supersedes,
        derived_from=list(draft.derived_from),
        sensitivity_class=str(draft.sensitivity["class"]),
        sensitivity=draft.sensitivity,
    )
    session.add(row)
    _emit(session, row)
    session.flush()
    return row


def latest(session: Session, record_id: str) -> MemoryRecord:
    row = session.scalars(
        select(MemoryRecord)
        .where(MemoryRecord.record_id == record_id)
        .order_by(MemoryRecord.version.desc())
        .limit(1)
    ).first()
    if row is None:
        raise RecordNotFoundError(record_id)
    return row


def transition(
    session: Session,
    *,
    record_id: str,
    to: Status,
    approved_by: dict[str, Any] | None = None,
    authority: int | None = None,
    enforcement: str | None = None,
    valid_from: dt.datetime | None = None,
    valid_to: dt.datetime | None = None,
) -> MemoryRecord:
    """Append the next version of `record_id` with status `to`.

    Approval may set the final authority, enforcement and validity (the
    approver decides, docs/discovery.md P5; issue 33); every other field is
    carried over unchanged."""
    current = latest(session, record_id)
    if to not in _TRANSITIONS[Status(current.status)]:
        raise IllegalTransitionError(f"cannot move a record from {current.status} to {to}")
    if to is Status.APPROVED and approved_by is None:
        raise IllegalTransitionError("approving needs approved_by")
    changes = (authority, enforcement, valid_from, valid_to)
    if to is not Status.APPROVED and any(c is not None for c in changes):
        raise IllegalTransitionError(
            "only an approval may change authority, enforcement or validity"
        )
    new_from = current.valid_from if valid_from is None else valid_from
    new_to = current.valid_to if valid_to is None else valid_to
    if new_to is not None and new_to <= new_from:
        raise IllegalTransitionError("valid_to must be after valid_from")
    new_authority = current.authority if authority is None else authority
    new_approved_by = approved_by if to is Status.APPROVED else current.approved_by
    _check_ceiling(current.asserted_by, new_authority, new_approved_by)
    row = MemoryRecord(
        **{
            c: getattr(current, c)
            for c in MemoryRecord.__table__.columns.keys()  # noqa: SIM118 -- ColumnCollection, not a dict
            if c
            not in (
                "id",
                "version",
                "recorded_at",
                "status",
                "approved_by",
                "authority",
                "enforcement",
                "valid_from",
                "valid_to",
            )
        },
        version=current.version + 1,
        valid_from=new_from,
        valid_to=new_to,
        status=to.value,
        approved_by=new_approved_by,
        authority=new_authority,
        enforcement=current.enforcement if enforcement is None else enforcement,
    )
    session.add(row)
    _emit(session, row)
    session.flush()
    return row


def supersede(
    session: Session,
    *,
    tenant_id: str,
    old_record_id: str,
    draft: RecordDraft,
    approved_by: dict[str, Any],
    codec: BodyCodec | None = None,
) -> MemoryRecord:
    """A new approved record replaces `old_record_id`, which becomes superseded."""
    transition(session, record_id=old_record_id, to=Status.SUPERSEDED)
    return append_new(
        session,
        tenant_id=tenant_id,
        draft=draft,
        status=Status.APPROVED,
        approved_by=approved_by,
        supersedes=old_record_id,
        codec=codec,
    )


def as_of(
    session: Session,
    record_id: str,
    *,
    system_time: dt.datetime,
    business_time: dt.datetime | None = None,
) -> MemoryRecord | None:
    """The version the ledger held at `system_time`; None if it did not exist
    yet, or if its validity window excludes `business_time`."""
    row = session.scalars(
        select(MemoryRecord)
        .where(MemoryRecord.record_id == record_id, MemoryRecord.recorded_at <= system_time)
        .order_by(MemoryRecord.version.desc())
        .limit(1)
    ).first()
    if row is None or business_time is None:
        return row
    return row if is_valid_at(row, business_time) else None


def is_valid_at(row: MemoryRecord, t: dt.datetime) -> bool:
    return row.valid_from <= t and (row.valid_to is None or t < row.valid_to)


def _record_key_ref(record_id: str) -> str:
    """Whose data key seals this body: the record's own (Q10, DEK per record).

    Issue 12 shared the lineage root's key with derived records. Issue 24
    found that this makes a derived record impossible to erase without its
    root, and lets a record derived from several parents survive the erasure
    of any parent but the first. A key per record, plus erasure that follows
    `derived_from` to every descendant (app/services/erasure.py), does both."""
    return record_id


def open_body(session: Session, row: MemoryRecord, codec: BodyCodec | None = None) -> Body:
    """The plaintext body, opened with the codec the row names."""
    if codec is not None and row.body_codec != codec.name:
        raise ValueError(f"row sealed with {row.body_codec}, opened with {codec.name}")
    codec = codec or codec_for(row.body_codec)
    key_ref = row.key_ref or row.record_id
    return codec.open(session, tenant_id=row.tenant_id, key_ref=key_ref, sealed=row.body_sealed)
