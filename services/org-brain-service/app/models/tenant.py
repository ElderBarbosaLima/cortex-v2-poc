"""Tenants this service holds data for: ids only, no tenant data, so no RLS.

The outbox drainer iterates it (the outbox itself is per-tenant under RLS).
Rows come from the tenant seed (issue 13)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UuidCol


class Tenant(Base):
    __tablename__ = "tenant"

    tenant_id: Mapped[str] = mapped_column(UuidCol, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
