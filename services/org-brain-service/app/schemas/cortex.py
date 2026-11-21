"""Wire schemas of the /v1/cortex contract (D15). UUIDs only, never numeric keys.

The OpenAPI document generated from these (libraries/cortex-contract/
openapi.json) is the single spec: REST today, MCP tools later from the
operations marked `x-mcp-tool`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class WhoAmI(BaseModel):
    consumer_id: str
    tenant_id: uuid.UUID
    end_user_id: uuid.UUID | None
    scopes: list[str]


class RecordSummary(BaseModel):
    record_id: uuid.UUID
    version: int = Field(description="Version of the record (1, 2, …), not a database key")
    kind: Literal["fact", "rule", "decision", "content"]
    status: Literal["proposed", "approved", "stale", "deprecated", "superseded", "erased"]
    scope: uuid.UUID
    enforcement: Literal["mandatory", "prohibition", "default"] | None
    authority: int = Field(ge=0, le=40, description="Authority ladder rung (0–40), not a key")
    sensitivity_class: Literal["public", "internal", "confidential", "restricted"]


class RecordRead(RecordSummary):
    policy_revision: str | None


class RecordPage(BaseModel):
    items: list[RecordSummary]
    next_cursor: str | None = Field(
        description="Opaque; pass it back as `cursor` for the next page, null at the end"
    )
    policy_revision: str | None


# ── Broad operations (MCP tools later; implemented in issues 20–22) ─────────


class ContextPackRequest(BaseModel):
    domain: str = Field(description="Domain pack name, e.g. custom_invoices")
    case: dict[str, Any] = Field(description="The case, in the domain pack's format")
    scope: uuid.UUID = Field(description="Scope node the work happens at (reads resolve upward)")
    purpose: str
    as_of: dt.date = Field(description="Business date of the event: rules apply as of this date")
    query: str | None = Field(default=None, description="Optional extra search text")


class PackRecord(BaseModel):
    record_id: uuid.UUID
    version: int
    kind: str
    enforcement: str | None
    role: Literal["constraint", "applied", "overridden", "candidate"]
    overridden_by: uuid.UUID | None = None
    body_hash: str
    body: dict[str, Any]


class ContextPack(BaseModel):
    pack_id: uuid.UUID
    trace_id: str
    domain: str
    as_of: dt.date
    policy_revision: str | None
    records: list[PackRecord]


class VerifyRequest(BaseModel):
    pack_id: uuid.UUID
    output: dict[str, Any] = Field(description="What the agent is about to post")


class HoldReason(BaseModel):
    reason: Literal["rule_no_longer_approved", "new_mandatory_rule", "output_check_failed"]
    record_id: uuid.UUID | None = None
    detail: str | None = None


class VerifyResult(BaseModel):
    pack_id: uuid.UUID
    result: Literal["pass", "hold"]
    reasons: list[HoldReason]


class CorrectionRequest(BaseModel):
    pack_id: uuid.UUID
    domain: str
    case_id: str
    correction: dict[str, Any] = Field(description="Proposal vs final, in the domain pack's format")
    idempotency_key: str = Field(min_length=8, max_length=128)


class CorrectionAccepted(BaseModel):
    decision_record_id: uuid.UUID
    proposed_record_id: uuid.UUID | None
    review_case_id: uuid.UUID | None
