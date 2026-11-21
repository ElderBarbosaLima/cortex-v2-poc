"""Declarative base and column types with SQLite variants (monorepo pattern).

Postgres is the system of record; the SQLite variants exist only so hermetic
unit tests can `create_all()` (CLAUDE.md: "Postgres-only column types need a
SQLite variant").
"""

from sqlalchemy import JSON, BigInteger, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase

BigInt = BigInteger().with_variant(Integer(), "sqlite")
JsonCol = JSONB().with_variant(JSON(), "sqlite")
UuidCol = UUID(as_uuid=False).with_variant(String(36), "sqlite")
UuidArray = ARRAY(UUID(as_uuid=False)).with_variant(JSON(), "sqlite")
TextArray = ARRAY(Text()).with_variant(JSON(), "sqlite")
Bytes = BYTEA().with_variant(LargeBinary(), "sqlite")


class Base(DeclarativeBase):
    pass
