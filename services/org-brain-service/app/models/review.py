"""Review queue tables (migrations 004 and 005)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, UuidArray, UuidCol


class ReviewCase(Base):
    __tablename__ = "review_case"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(UuidCol, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    conflict_key: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(UuidCol, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewCaseItem(Base):
    __tablename__ = "review_case_item"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    case_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    record_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewDecision(Base):
    """One decision on a case. Append-only (migration 005)."""

    __tablename__ = "review_decision"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(UuidCol, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    case_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[str] = mapped_column(UuidCol, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_record_id: Mapped[str | None] = mapped_column(UuidCol, nullable=True)
    record_ids: Mapped[list[str]] = mapped_column(UuidArray, nullable=False)
    authority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    enforcement: Mapped[str | None] = mapped_column(String(16), nullable=True)
    policy_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
