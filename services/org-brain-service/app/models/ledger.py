"""The ledger: one append-only table of memory_record versions (Rev. B rules 1–3).

Each row is one immutable version of a record. A status change appends the next
version of the same `record_id`; superseding creates a new `record_id` whose
`supersedes` points at the old one. The current state is the highest version
(`memory_record_current` view). DDL, row-level security and the append-only
trigger live in migration 001; this module mirrors the columns for the ORM.

The 19 envelope fields map to columns as follows (docs/discovery.md 1.2):
`body` is stored sealed (`body_sealed`, `body_codec`, `body_hash`) and
`sensitivity` is split into a filterable `sensitivity_class` plus the full
JSON. Everything else is one column each.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Index, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, Bytes, JsonCol, TextArray, UuidArray, UuidCol


class Kind(StrEnum):
    FACT = "fact"
    RULE = "rule"
    DECISION = "decision"
    CONTENT = "content"


class Status(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    STALE = "stale"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    ERASED = "erased"


class Enforcement(StrEnum):
    MANDATORY = "mandatory"
    PROHIBITION = "prohibition"
    DEFAULT = "default"


class SensitivityClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class AssuranceLevel(StrEnum):
    UNVERIFIED = "unverified"
    REVIEWED = "reviewed"
    ATTESTED = "attested"


class MemoryRecord(Base):
    __tablename__ = "memory_record"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    body_sealed: Mapped[bytes] = mapped_column(Bytes, nullable=False)
    body_codec: Mapped[str] = mapped_column(String(32), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # The lineage root whose data key seals the body (migration 003).
    key_ref: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    entities: Mapped[list[str]] = mapped_column(TextArray, nullable=False, default=list)
    scope: Mapped[str] = mapped_column(UuidCol, nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JsonCol, nullable=False)
    asserted_by: Mapped[dict[str, Any]] = mapped_column(JsonCol, nullable=False)
    approved_by: Mapped[dict[str, Any] | None] = mapped_column(JsonCol, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    enforcement: Mapped[str | None] = mapped_column(String(16), nullable=True)
    authority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    assurance_level: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    supersedes: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    derived_from: Mapped[list[str]] = mapped_column(UuidArray, nullable=False, default=list)
    sensitivity_class: Mapped[str] = mapped_column(String(16), nullable=False)
    sensitivity: Mapped[dict[str, Any]] = mapped_column(JsonCol, nullable=False)

    __table_args__ = (
        UniqueConstraint("record_id", "version", name="uq_memory_record_version"),
        Index("ix_memory_record_tenant_record", "tenant_id", "record_id", "version"),
    )


class RecordKey(Base):
    """One wrapped data key per lineage root (migration 003). The only ledger-side
    table rows are ever deleted from: deleting one is crypto-shredding (Q10)."""

    __tablename__ = "record_key"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    key_ref: Mapped[str] = mapped_column(UuidCol, nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(Bytes, nullable=False)
    kek_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("tenant_id", "key_ref", name="uq_record_key_ref"),)


class Outbox(Base):
    """Projection and job events, written in the same transaction as the ledger
    row they describe (shape of project-orchestrator's `Outbox`, RAY-81898)."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonCol, nullable=False)
    subject_record_id: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
