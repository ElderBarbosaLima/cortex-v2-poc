"""Served context packs (migration 006). Append-only."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, Bytes, UuidCol


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
