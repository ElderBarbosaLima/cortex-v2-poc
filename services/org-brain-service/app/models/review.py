"""Review queue tables (migration 004)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigInt, UuidCol


class ReviewCase(Base):
    __tablename__ = "review_case"

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(UuidCol, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(UuidCol, nullable=False)
    conflict_key: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(UuidCol, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
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
