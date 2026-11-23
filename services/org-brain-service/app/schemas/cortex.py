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
    body_hash: str = Field(description="sha256 of the body as served; survives erasure")
    body: dict[str, Any] | None = Field(
        description="Null when the record was erased after the pack was served"
    )
    body_erased: bool = False


class PackBuiltWith(BaseModel):
    """What built the pack: a stored pack keeps the values it was served with."""

    projection: str | None
    embedding_model: str | None = Field(description="Null when embeddings were off")


class ContextPack(BaseModel):
    """As served, and as stored: `GET /context-packs/{pack_id}` returns the same."""

    pack_id: uuid.UUID
    trace_id: str
    domain: str
    scope: uuid.UUID
    purpose: str
    as_of: dt.date
    policy_revision: str | None
    served_at: dt.datetime
    built_with: PackBuiltWith
    records: list[PackRecord] = Field(
        description="Constraints, then applied and overridden defaults (from the ledger, never "
        "search), then knowledge candidates found by search and revalidated against the ledger"
    )


class VerifyRequest(BaseModel):
    pack_id: uuid.UUID
    case: dict[str, Any] = Field(
        description="The same case the pack was served for (checked by hash; Cortex keeps "
        "only the hash), so applicability can be re-run as of the pack's business date"
    )
    output: dict[str, Any] = Field(description="What the agent is about to post")


class HoldReason(BaseModel):
    reason: Literal[
        "rule_no_longer_approved",
        "new_mandatory_rule",
        "constraint_unevaluable",
        "output_check_failed",
    ]
    record_id: uuid.UUID | None = None
    detail: str | None = None


class VerifyResult(BaseModel):
    verification_id: uuid.UUID = Field(description="The stored, append-only result")
    pack_id: uuid.UUID
    result: Literal["pass", "hold"]
    reasons: list[HoldReason]
    policy_revision: str | None
    verified_at: dt.datetime


class CorrectionRequest(BaseModel):
    pack_id: uuid.UUID
    domain: str = Field(description="Must match the pack's domain")
    case_id: str = Field(min_length=1, max_length=128, description="The work case reference")
    case: dict[str, Any] = Field(
        description="The case the pack was served for (checked by hash; Cortex keeps only "
        "the hash). The domain pack turns the correction into a rule draft from it"
    )
    correction: dict[str, Any] = Field(description="Proposal vs final, in the domain pack's format")
    idempotency_key: str = Field(min_length=8, max_length=128)


class RuleSuggestion(BaseModel):
    """D17: whether this correction (with earlier ones on the same conflict) is
    enough to propose a rule yet."""

    met: bool
    distinct_cases: int
    distinct_correctors: int
    min_distinct_cases: int
    min_distinct_correctors: int
    window_days: int


class CorrectionAccepted(BaseModel):
    correction_id: uuid.UUID
    decision_record_id: uuid.UUID = Field(description="The kind=decision record of the correction")
    proposed_record_id: uuid.UUID | None = Field(
        description="The proposed rule, when the thresholds were met; a person must approve it"
    )
    review_case_id: uuid.UUID | None
    replayed: bool = Field(description="True when this idempotency key was seen before")
    suggested_by: Literal["policy", "model"] | None = Field(
        default=None,
        description="Who drafted the proposed rule: the domain pack (policy) or a model "
        "through LiteLLM; null when nothing was proposed",
    )
    rule_suggestion: RuleSuggestion | None = Field(description="Null on a replay")
    proposal_problems: list[dict[str, str]] = Field(
        default_factory=list[dict[str, str]],
        description="Why the domain pack's draft could not be proposed (admission problems)",
    )


# ── Admission (issue 17) ────────────────────────────────────────────────────


class Sensitivity(BaseModel):
    class_: Literal["public", "internal", "confidential", "restricted"] = Field(alias="class")
    personal_data: bool = False
    retention_days: int | None = Field(default=None, ge=1)


class ProposeRecordRequest(BaseModel):
    domain: str = Field(description="Domain pack name, e.g. custom_invoices")
    kind: Literal["fact", "rule", "decision", "content"]
    schema_version: str = Field(description="<domain>.<kind>/1")
    body: dict[str, Any]
    scope: uuid.UUID
    entities: list[str] = Field(
        default_factory=list[str], description="Canonical keys, e.g. customer:CUST-0042"
    )
    enforcement: Literal["mandatory", "prohibition", "default"] | None = None
    authority: int = Field(default=10, ge=0, le=40)
    sensitivity: Sensitivity
    purpose: str = Field(description="Purpose the proposal is made under (sensitivity ceiling)")
    valid_from: dt.date = Field(description="Business date from which it holds")
    valid_to: dt.date | None = None
    source: dict[str, Any] = Field(default_factory=dict[str, Any])
    derived_from: list[uuid.UUID] = Field(default_factory=list[uuid.UUID])
    supersedes: uuid.UUID | None = Field(
        default=None,
        description="An approved record this one replaces once a named owner approves it",
    )


class ProposalAccepted(BaseModel):
    record_id: uuid.UUID
    version: int
    status: Literal["proposed"]
    review_case_id: uuid.UUID | None
    grouped: bool = Field(
        description="True when the proposal joined an open case for the same conflict"
    )
    warnings: list[dict[str, str]] = Field(
        default_factory=list[dict[str, str]],
        description="Admitted, but the owner is told: e.g. loosens_inherited_constraint",
    )


# ── Review queue (issue 18) ─────────────────────────────────────────────────

Outcome = Literal["pending", "approved", "rejected", "retired", "superseded"]
Enforcement = Literal["mandatory", "prohibition", "default"]


class NamedOwners(BaseModel):
    owner: uuid.UUID
    backup: uuid.UUID | None


class ReviewCaseSummary(BaseModel):
    case_id: uuid.UUID
    scope: uuid.UUID
    title: str = Field(description="From the first proposal's sealed body; never stored in clear")
    status: Literal["open", "decided"]
    opened_at: dt.datetime
    proposal_count: int
    sensitivity_class: Literal["public", "internal", "confidential", "restricted"]
    named_owners: NamedOwners | None = Field(
        description="Who may decide: the nearest owned scope at or above this one"
    )


class ReviewCasePage(BaseModel):
    items: list[ReviewCaseSummary]
    next_cursor: str | None
    policy_revision: str | None


class CaseProposal(RecordSummary):
    outcome: Outcome = Field(description="Derived from history: rejected = never approved")
    asserted_by: dict[str, Any]
    approved_by: dict[str, Any] | None
    body: dict[str, Any] | None = Field(description="Null once the record was erased")
    body_erased: bool = False
    body_hash: str
    entities: list[str]
    source: dict[str, Any] = Field(description="Evidence: where the proposal came from")
    derived_from: list[uuid.UUID]
    supersedes: uuid.UUID | None = None
    valid_from: dt.datetime
    valid_to: dt.datetime | None


class DecisionView(BaseModel):
    decision_id: uuid.UUID = Field(description="Also the record_id of the kind=decision record")
    decision: Literal["approve", "reject"]
    decided_by: uuid.UUID
    reason: str | None
    approved_record_id: uuid.UUID | None
    record_ids: list[uuid.UUID]
    authority: int | None
    enforcement: Enforcement | None
    policy_revision: str | None
    decided_at: dt.datetime


class DecisionPermission(BaseModel):
    allowed: bool
    reason: str | None = Field(description="OPA's reason, the same a decision would get")
    policy_revision: str | None


class CanDecide(BaseModel):
    """What the caller (the end user, or the consumer alone) may do on this
    case now: the approval policy asked ahead of time, so a UI can show a
    disabled control with the reason. The decision itself is checked again."""

    approve: DecisionPermission
    reject: DecisionPermission


class ReviewCaseDetail(ReviewCaseSummary):
    proposals: list[CaseProposal]
    can_decide: CanDecide
    affected_cases: list[str] = Field(
        description="Work cases the proposals came from (source.case_ref / source.case_refs)"
    )
    contributors: list[uuid.UUID] = Field(
        description="People behind the proposals: none of them may decide on this case"
    )
    decisions: list[DecisionView]
    policy_revision: str | None


class ApproveRequest(BaseModel):
    record_id: uuid.UUID | None = Field(
        default=None,
        description="Which pending proposal to approve; default the first. The other "
        "pending proposals in the case become superseded",
    )
    authority: int = Field(ge=0, le=40, description="Final authority, set by the approver")
    enforcement: Enforcement | None = Field(
        default=None, description="Final enforcement (rules only); default the proposed one"
    )
    reason: str | None = Field(default=None, max_length=2000)
    valid_from: dt.date | None = Field(
        default=None, description="Final business date from which it holds (default: as proposed)"
    )
    valid_to: dt.date | None = Field(
        default=None, description="Business date it stops holding (exclusive); null: no end"
    )


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class DecidedRecord(BaseModel):
    record_id: uuid.UUID
    version: int
    status: Literal["proposed", "approved", "stale", "deprecated", "superseded", "erased"]
    outcome: Outcome


class DecisionResult(BaseModel):
    decision_id: uuid.UUID
    case_id: uuid.UUID
    decision: Literal["approve", "reject"]
    records: list[DecidedRecord]
    policy_revision: str | None


# ── Evidence export (issue 23) ──────────────────────────────────────────────


class EvidenceExportView(BaseModel):
    export_id: uuid.UUID
    pack_id: uuid.UUID
    status: Literal["pending", "exported", "failed"]
    bucket: str
    object_key: str = Field(description="Deterministic: tenants/<tenant>/context-packs/<pack>.json")
    manifest_sha256: str | None = Field(description="sha256 of the manifest before encryption")
    version_id: str | None
    retention_mode: str | None
    retain_until: dt.datetime | None
    requested_at: dt.datetime
    exported_at: dt.datetime | None


class RetireRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class RetireResult(BaseModel):
    decision_id: uuid.UUID = Field(description="The kind=decision record of the retirement")
    record_id: uuid.UUID
    version: int
    status: Literal["deprecated"]
    outcome: Literal["retired"]
    policy_revision: str | None


# ── Expiry (issue 33) ──────────────────────────────────────────────────────


class ExpiringRecord(RecordSummary):
    valid_to: dt.datetime
    expired: bool = Field(
        description="valid_to has passed: packs for later dates no longer carry it"
    )


class ExpiringPage(BaseModel):
    items: list[ExpiringRecord]
    within_days: int
    policy_revision: str | None


# ── Imports (issue 37, scenario 1) ──────────────────────────────────────────


class ImportRequest(BaseModel):
    source_system: str = Field(
        min_length=1, max_length=120, description="Where the batch comes from"
    )
    items: list[ProposeRecordRequest] = Field(min_length=1, max_length=500)


class ImportItemResult(BaseModel):
    index: int
    status: Literal["proposed", "duplicate", "rejected"]
    record_id: uuid.UUID | None = None
    review_case_id: uuid.UUID | None = None
    expired: bool = Field(
        default=False, description="Already past valid_to: never applies, even if approved"
    )
    supersedes: uuid.UUID | None = Field(
        default=None, description="The approved record this item would replace once approved"
    )
    error: str | None = None
    problems: list[dict[str, str]] = Field(default_factory=list[dict[str, str]])


class ImportResult(BaseModel):
    source_system: str
    items: list[ImportItemResult]
