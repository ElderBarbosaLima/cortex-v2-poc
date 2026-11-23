"""Served context packs (006), their verifications (007) and corrections (008).

All append-only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import Date, DateTime, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, Bytes, JsonCol, UuidCol


class ServedPack(Base):
    __tablename__ = "served_pack"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    pack_id: Mapped[str] = mapped_column(UuidCol, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    consumer_id: Mapped[str] = mapped_column(Text, nullable=False)
    end_user_id: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(UuidCol, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    policy_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    served_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ServedPackItem(Base):
    __tablename__ = "served_pack_item"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    pack_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    record_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enforcement: Mapped[str | None] = mapped_column(String(16), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    overridden_by: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body_sealed: Mapped[bytes] = mapped_column(Bytes, nullable=False)
    body_codec: Mapped[str] = mapped_column(String(32), nullable=False)
    key_ref: Mapped[str] = mapped_column(UuidCol, nullable=False)


class Verification(Base):
    """One release check of an output against a served pack (migration 007)."""

    __tablename__ = "verification"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    verification_id: Mapped[str] = mapped_column(UuidCol, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    pack_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    consumer_id: Mapped[str] = mapped_column(Text, nullable=False)
    end_user_id: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    result: Mapped[str] = mapped_column(String(8), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JsonCol, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Correction(Base):
    """A reviewer's correction of an output (migration 008). Append-only."""

    __tablename__ = "correction"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    correction_id: Mapped[str] = mapped_column(UuidCol, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pack_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    case_ref: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_by: Mapped[str] = mapped_column(UuidCol, nullable=False)
    conflict_key: Mapped[str] = mapped_column(Text, nullable=False)
    decision_record_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    proposed_record_id: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    review_case_id: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
