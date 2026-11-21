"""Test builders for ledger rows (shared by unit and integration tests)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from app.models import MemoryRecord
from app.utils.ids import new_record_id

TENANT_A = "0b1e1c1a-0000-4000-8000-00000000000a"
TENANT_B = "0b1e1c1a-0000-4000-8000-00000000000b"
SCOPE = "0b1e1c1a-0000-4000-8000-0000000005c0"


def memory_record(
    tenant_id: str = TENANT_A,
    *,
    record_id: str | None = None,
    version: int = 1,
    status: str = "approved",
    **overrides: Any,
) -> MemoryRecord:
    body = json.dumps({"text_ja": "テスト用ルール"}, ensure_ascii=False).encode()
    fields: dict[str, Any] = {
        "record_id": record_id or new_record_id(),
        "version": version,
        "tenant_id": tenant_id,
        "kind": "rule",
        "schema_version": "test.rule/1",
        "body_sealed": body,
        "body_codec": "plain-json/1",
        "body_hash": hashlib.sha256(body).hexdigest(),
        "entities": [],
        "scope": SCOPE,
        "source": {"system": "test"},
        "asserted_by": {"type": "system", "id": "test"},
        "approved_by": {"approver_type": "user", "user": "test"} if status == "approved" else None,
        "status": status,
        "enforcement": "mandatory",
        "authority": 30,
        "assurance_level": "reviewed",
        "valid_from": dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
        "sensitivity_class": "internal",
        "sensitivity": {"class": "internal", "personal_data": False},
    }
    fields.update(overrides)
    return MemoryRecord(**fields)
