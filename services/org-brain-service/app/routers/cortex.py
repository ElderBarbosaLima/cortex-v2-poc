"""/v1/cortex — the consumer API (D15), generated into libraries/cortex-contract.

Same prefix and authentication shape as integrations-service's /v1, so moving
these routes there later is a change of host, not of contract. The three broad
operations an agent needs are marked `x-mcp-tool`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ERROR_RESPONSES, ApiError, CortexApiError
from app.domain_packs.base import UnknownPackError, get_pack
from app.identity import AuthedCaller, caller_session, require_scope
from app.models import Kind
from app.schemas.cortex import (
    ContextPack,
    ContextPackRequest,
    CorrectionAccepted,
    CorrectionRequest,
    EvidenceExportView,
    HoldReason,
    ProposalAccepted,
    ProposeRecordRequest,
    RecordPage,
    RecordRead,
    RecordSummary,
    RetireRequest,
    RetireResult,
    RuleSuggestion,
    VerifyRequest,
    VerifyResult,
    WhoAmI,
)
from app.services import admission, evidence, ledger, review, writeback
from app.services.access_gate import context_pack, verify
from app.services.access_gate.applicability import case_facts
from app.services.policy_gate import PolicyGate, now_rfc3339
from app.utils.business_dates import JST, business_start

_logger = structlog.get_logger("org-brain.cortex")
router = APIRouter(prefix="/v1/cortex", tags=["cortex"], responses=ERROR_RESPONSES)

SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
MCP_TOOLS = ("request_context_pack", "verify_output", "submit_correction")


def _mcp(name: str, description: str) -> dict[str, Any]:
    return {"x-mcp-tool": {"name": name, "description": description}}


def read_payload(caller: AuthedCaller, *, purpose: str, scope_node: str) -> dict[str, Any]:
    return {
        "tenant_id": caller.tenant_id,
        "consumer": {"id": caller.consumer_id},
        "end_user": {"id": caller.end_user_id} if caller.end_user_id else None,
        "purpose": purpose,
        "scope_node": scope_node,
        "now": now_rfc3339(),
    }


def authorise(request: Request, caller: AuthedCaller, *, purpose: str, scope: str) -> Any:
    gate: PolicyGate = request.app.state.policy_gate
    decision = gate.read(read_payload(caller, purpose=purpose, scope_node=scope))
    if not decision.allow:
        raise CortexApiError(
            403, "access_denied", decision.reason, policy_revision=decision.policy_revision
        )
    return decision


def _summary(row: Any) -> RecordSummary:
    return RecordSummary.model_validate(row, from_attributes=True)


@router.get("/whoami", response_model=WhoAmI, operation_id="whoami")
def whoami(caller: AuthedCaller = Depends(require_scope("cortex:read"))) -> WhoAmI:
    return WhoAmI.model_validate(caller.model_dump())


@router.get("/records", response_model=RecordPage, operation_id="list_records")
def list_records(
    request: Request,
    scope: uuid.UUID = Query(description="Scope node to read at; resolves upward"),
    purpose: str = Query(min_length=1),
    kind: str | None = Query(default=None),
    status: str = Query(default="approved"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: uuid.UUID | None = Query(default=None),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> RecordPage:
    """Current versions visible at `scope` for `purpose`, ordered by record id
    (ULIDs: creation order). Only scopes OPA allows, never above the purpose's
    sensitivity ceiling; RLS keeps the tenant."""
    decision = authorise(request, caller, purpose=purpose, scope=str(scope))
    allowed = [
        s
        for s, rank in SENSITIVITY_RANK.items()
        if rank <= SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    ]
    rows = session.execute(
        text(
            "SELECT record_id::text AS record_id, version, kind, status, scope::text AS scope, "
            "enforcement, authority, sensitivity_class FROM memory_record_current "
            "WHERE scope = ANY(CAST(:scopes AS uuid[])) AND status = :status "
            "AND (CAST(:kind AS text) IS NULL OR kind = :kind) "
            "AND sensitivity_class = ANY(CAST(:allowed AS text[])) "
            "AND (CAST(:cursor AS uuid) IS NULL OR record_id > CAST(:cursor AS uuid)) "
            "ORDER BY record_id LIMIT :limit"
        ),
        {
            "scopes": decision.readable_scopes,
            "status": status,
            "kind": kind,
            "allowed": allowed,
            "cursor": str(cursor) if cursor else None,
            "limit": limit + 1,
        },
    ).all()
    items = [_summary(r) for r in rows[:limit]]
    next_cursor = str(items[-1].record_id) if len(rows) > limit else None
    return RecordPage(
        items=items, next_cursor=next_cursor, policy_revision=decision.policy_revision
    )


@router.get("/records/{record_id}", response_model=RecordRead, operation_id="get_record")
def get_record(
    record_id: uuid.UUID,
    request: Request,
    purpose: str = Query(min_length=1),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> RecordRead:
    """One record's current version. Another tenant's record is a 404, exactly
    like a missing one: no existence oracle across tenants."""
    try:
        row = ledger.latest(session, str(record_id))
    except ledger.RecordNotFoundError:
        raise CortexApiError(404, "not_found", "no such record") from None
    decision = authorise(request, caller, purpose=purpose, scope=row.scope)
    ceiling = SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    if SENSITIVITY_RANK[row.sensitivity_class] > ceiling:
        raise CortexApiError(
            403,
            "access_denied",
            f"sensitivity {row.sensitivity_class} is above this purpose's ceiling",
            policy_revision=decision.policy_revision,
        )
    return RecordRead(**_summary(row).model_dump(), policy_revision=decision.policy_revision)


def _business_start(day: dt.date | None) -> dt.datetime | None:
    return None if day is None else business_start(day)


@router.post(
    "/records",
    response_model=ProposalAccepted,
    status_code=201,
    operation_id="propose_record",
    responses={422: {"model": ApiError, "description": "Invalid proposal: every problem listed"}},
)
def propose_record(
    body: ProposeRecordRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> ProposalAccepted:
    """The admission gate: validate, authorise, then append as `proposed` and
    route to the review queue (one open case per conflict). Never approved here."""
    proposal = admission.Proposal(
        domain=body.domain,
        kind=Kind(body.kind),
        schema_version=body.schema_version,
        body=body.body,
        scope=str(body.scope),
        entities=tuple(body.entities),
        enforcement=body.enforcement,
        authority=body.authority,
        sensitivity=body.sensitivity.model_dump(by_alias=True),
        valid_from=_business_start(body.valid_from) or dt.datetime.now(JST),
        valid_to=_business_start(body.valid_to),
        source=body.source,
        derived_from=tuple(str(d) for d in body.derived_from),
    )
    _, problems = admission.validate_proposal(proposal)
    if problems:
        raise CortexApiError(422, "invalid_proposal", admission.problems_payload(problems))

    read = authorise(request, caller, purpose=body.purpose, scope=proposal.scope)
    ceiling = admission.SENSITIVITY_RANK.get(read.sensitivity_ceiling or "", -1)
    if admission.SENSITIVITY_RANK[body.sensitivity.class_] > ceiling:
        raise CortexApiError(
            403,
            "access_denied",
            f"sensitivity {body.sensitivity.class_} is above the "
            f"{read.sensitivity_ceiling} ceiling for purpose {body.purpose!r}",
            policy_revision=read.policy_revision,
        )
    actor = (
        {"type": "user", "id": caller.end_user_id}
        if caller.end_user_id
        else {"type": "consumer", "id": caller.consumer_id}
    )
    gate: PolicyGate = request.app.state.policy_gate
    permit = gate.approval(
        {
            "tenant_id": caller.tenant_id,
            "action": "propose",
            "actor": actor,
            "record": {"scope": proposal.scope, "contributors": []},
            "now": now_rfc3339(),
        }
    )
    if not permit.allow:
        raise CortexApiError(
            403, "access_denied", permit.reason, policy_revision=permit.policy_revision
        )

    # A person behind the call asserts it; an agent alone asserts as a model
    # (authority capped at the model ceiling until a person approves).
    asserted_by = (
        {"type": "user", "id": caller.end_user_id}
        if caller.end_user_id
        else {"type": "model", "id": caller.consumer_id}
    )
    try:
        admitted = admission.admit(
            session, tenant_id=caller.tenant_id, proposal=proposal, asserted_by=asserted_by
        )
    except ledger.ModelCeilingError as exc:
        raise CortexApiError(
            422, "invalid_proposal", [{"code": "model_ceiling", "detail": str(exc)}]
        ) from None
    return ProposalAccepted(
        record_id=admitted.record.record_id,
        version=admitted.record.version,
        status="proposed",
        review_case_id=uuid.UUID(admitted.review_case_id) if admitted.review_case_id else None,
        grouped=admitted.grouped,
    )


@router.post(
    "/records/{record_id}/retire",
    response_model=RetireResult,
    operation_id="retire_record",
    responses={
        404: {"model": ApiError, "description": "No such record (or another tenant's)"},
        409: {"model": ApiError, "description": "Not approved: nothing to retire"},
    },
)
def retire_record(
    record_id: uuid.UUID,
    body: RetireRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> RetireResult:
    """The named owner (or backup) withdraws an approved record, with a reason.
    Packs that relied on it are held by verify from then on."""
    try:
        current = ledger.latest(session, str(record_id))
    except ledger.RecordNotFoundError:
        raise CortexApiError(404, "not_found", "no such record") from None
    gate: PolicyGate = request.app.state.policy_gate
    permit = gate.approval(
        {
            "tenant_id": caller.tenant_id,
            "action": "retire",
            "actor": (
                {"type": "user", "id": caller.end_user_id}
                if caller.end_user_id
                else {"type": "consumer", "id": caller.consumer_id}
            ),
            "record": {"scope": current.scope, "contributors": []},
            "now": now_rfc3339(),
        }
    )
    if not permit.allow:
        raise CortexApiError(
            403, "access_denied", permit.reason, policy_revision=permit.policy_revision
        )
    try:
        retired = review.retire(
            session,
            tenant_id=caller.tenant_id,
            record_id=str(record_id),
            user_id=str(caller.end_user_id),
            reason=body.reason,
            policy_revision=permit.policy_revision,
        )
    except review.NotRetirableError as exc:
        raise CortexApiError(409, "not_retirable", str(exc)) from None
    return RetireResult(
        decision_id=uuid.UUID(retired.decision_id),
        record_id=record_id,
        version=retired.version.version,
        status="deprecated",
        outcome="retired",
        policy_revision=permit.policy_revision,
    )


def _pack_response(stored: context_pack.StoredPack) -> ContextPack:
    return context_pack.render(stored)


@router.post(
    "/context-packs",
    response_model=ContextPack,
    status_code=201,
    operation_id="request_context_pack",
    responses={
        409: {"model": ApiError, "description": "Held: a constraint cannot be honoured"},
    },
    openapi_extra=_mcp(
        "request_context_pack",
        "Get the governed context for one case: every applicable mandatory rule and "
        "prohibition, relevant approved knowledge, stored as a pack with an id to verify against.",
    ),
)
def request_context_pack(
    body: ContextPackRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> ContextPack:
    """Authorise (OPA) → rules by applicability and resolution (never search) →
    knowledge by search, each hit revalidated against the ledger → stored, then
    returned. Held (409) rather than served without a constraint."""
    try:
        pack = get_pack(body.domain)
    except UnknownPackError:
        raise CortexApiError(422, "unknown_domain", f"no domain pack {body.domain!r}") from None
    decision = authorise(request, caller, purpose=body.purpose, scope=str(body.scope))
    try:
        facts = case_facts(pack, body.case)
    except (KeyError, TypeError, ValueError) as exc:
        raise CortexApiError(422, "invalid_case", f"{type(exc).__name__}: {exc}") from None
    access = context_pack.Access(
        readable_scopes=tuple(decision.readable_scopes),
        sensitivity_ceiling=decision.sensitivity_ceiling,
        policy_revision=decision.policy_revision,
    )
    trace_id = _trace_id()
    try:
        built = context_pack.build_and_store(
            session,
            request=context_pack.PackRequest(
                tenant_id=caller.tenant_id,
                consumer_id=caller.consumer_id,
                end_user_id=str(caller.end_user_id) if caller.end_user_id else None,
                trace_id=trace_id,
                domain=body.domain,
                case=body.case,
                scope=str(body.scope),
                purpose=body.purpose,
                as_of=body.as_of,
                query=body.query,
            ),
            pack=pack,
            facts=facts,
            access=access,
            search=request.app.state.search,
        )
    except context_pack.PackHeldError as exc:
        raise CortexApiError(
            409, exc.error, exc.detail, policy_revision=decision.policy_revision
        ) from None
    if built.dropped:
        _logger.info(
            "context_pack.search_hits_dropped",
            pack_id=built.pack_id,
            dropped=[(d.record_id, d.reason) for d in built.dropped],
        )
    return _pack_response(context_pack.load(session, built.pack_id))


@router.get(
    "/context-packs/{pack_id}",
    response_model=ContextPack,
    operation_id="get_context_pack",
    responses={404: {"model": ApiError, "description": "No such pack (or another tenant's)"}},
)
def get_context_pack(
    pack_id: uuid.UUID,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> ContextPack:
    """Replay: the pack exactly as it was served, from storage, nothing re-run.
    Reading it is a read of its scope for its purpose, decided by OPA now."""
    try:
        stored = context_pack.load(session, str(pack_id))
    except context_pack.PackNotFoundError:
        raise CortexApiError(404, "not_found", "no such context pack") from None
    authorise(request, caller, purpose=stored.pack.purpose, scope=stored.pack.scope)
    return _pack_response(stored)


def _export_view(row: Any) -> EvidenceExportView:
    return EvidenceExportView.model_validate(row, from_attributes=True)


def _authorised_pack(
    request: Request, caller: AuthedCaller, session: Session, pack_id: uuid.UUID
) -> context_pack.StoredPack:
    try:
        stored = context_pack.load(session, str(pack_id))
    except context_pack.PackNotFoundError:
        raise CortexApiError(404, "not_found", "no such context pack") from None
    authorise(request, caller, purpose=stored.pack.purpose, scope=stored.pack.scope)
    return stored


@router.post(
    "/context-packs/{pack_id}/export",
    response_model=EvidenceExportView,
    status_code=202,
    operation_id="export_context_pack",
    responses={404: {"model": ApiError, "description": "No such pack (or another tenant's)"}},
)
def export_context_pack(
    pack_id: uuid.UUID,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> EvidenceExportView:
    """Archive the pack's manifest (pack, record versions and approvals, policy
    revision, verifications), encrypted under the tenant's key, to the
    evidence bucket with Object Lock retention. Asynchronous (outbox); asking
    again returns the same export."""
    stored = _authorised_pack(request, caller, session, pack_id)
    row, _ = evidence.request_export(
        session, stored=stored, bucket=request.app.state.evidence_bucket
    )
    return _export_view(row)


@router.get(
    "/context-packs/{pack_id}/export",
    response_model=EvidenceExportView,
    operation_id="get_context_pack_export",
    responses={404: {"model": ApiError, "description": "No export (or another tenant's)"}},
)
def get_context_pack_export(
    pack_id: uuid.UUID,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> EvidenceExportView:
    stored = _authorised_pack(request, caller, session, pack_id)
    row = evidence.find_export(session, stored.pack.pack_id)
    if row is None:
        raise CortexApiError(404, "not_found", "this pack has not been exported")
    return _export_view(row)


def _trace_id() -> str:
    return str(structlog.contextvars.get_contextvars().get("request_id") or uuid.uuid4())


@router.post(
    "/verify",
    response_model=VerifyResult,
    operation_id="verify_output",
    responses={404: {"model": ApiError, "description": "No such pack (or another tenant's)"}},
    openapi_extra=_mcp(
        "verify_output",
        "Before posting, check an output against its pack: every mandatory rule still "
        "approved and current, no new one since. Answers pass or hold with reasons.",
    ),
)
def verify_output(
    body: VerifyRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> VerifyResult:
    """Release check, on the ledger only: constraints still approved, no new
    constraint since, output satisfies the pack's rules. Stored; never blocks
    the client's system itself (D3)."""
    try:
        stored = context_pack.load(session, str(body.pack_id))
    except context_pack.PackNotFoundError:
        raise CortexApiError(404, "not_found", "no such context pack") from None
    decision = authorise(request, caller, purpose=stored.pack.purpose, scope=stored.pack.scope)
    pack = get_pack(stored.pack.domain)
    try:
        facts = case_facts(pack, body.case)
    except (KeyError, TypeError, ValueError) as exc:
        raise CortexApiError(422, "invalid_case", f"{type(exc).__name__}: {exc}") from None
    try:
        checked = verify.verify(
            session,
            stored=stored,
            pack=pack,
            facts=facts,
            case=body.case,
            output=body.output,
            access=context_pack.Access(
                readable_scopes=tuple(decision.readable_scopes),
                sensitivity_ceiling=decision.sensitivity_ceiling,
                policy_revision=decision.policy_revision,
            ),
            trace_id=_trace_id(),
            consumer_id=caller.consumer_id,
            end_user_id=str(caller.end_user_id) if caller.end_user_id else None,
        )
    except verify.CaseMismatchError as exc:
        raise CortexApiError(422, "case_mismatch", str(exc)) from None
    row = checked.verification
    return VerifyResult(
        verification_id=uuid.UUID(row.verification_id),
        pack_id=body.pack_id,
        result=checked.result,
        reasons=[HoldReason.model_validate(r) for r in row.reasons],
        policy_revision=row.policy_revision,
        verified_at=row.verified_at,
    )


@router.post(
    "/corrections",
    response_model=CorrectionAccepted,
    status_code=202,
    operation_id="submit_correction",
    responses={
        404: {"model": ApiError, "description": "No such pack (or another tenant's)"},
        409: {"model": ApiError, "description": "Idempotency key reused for another request"},
    },
    openapi_extra=_mcp(
        "submit_correction",
        "Report a reviewer's correction of an output (with its pack id). Cortex records "
        "the decision and may propose a rule for a named owner to approve.",
    ),
)
def submit_correction(
    body: CorrectionRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> CorrectionAccepted:
    """Writeback: record the correction as a decision; once the D17 thresholds
    are met, propose the domain pack's rule through the admission gate. The
    reviewer is the end user (X-End-User-Id). Idempotent by key."""
    if caller.end_user_id is None:
        raise CortexApiError(
            422, "reviewer_required", "a correction needs the reviewer as X-End-User-Id"
        )
    payload_hash = writeback.request_hash(body.model_dump(mode="json"))
    try:
        earlier = writeback.find(
            session,
            tenant_id=caller.tenant_id,
            idempotency_key=body.idempotency_key,
            payload_hash=payload_hash,
        )
    except writeback.IdempotencyConflictError as exc:
        raise CortexApiError(409, "idempotency_conflict", str(exc)) from None
    if earlier is not None:
        return _accepted(writeback.Outcome(earlier, None, replayed=True), None)
    try:
        stored = context_pack.load(session, str(body.pack_id))
    except context_pack.PackNotFoundError:
        raise CortexApiError(404, "not_found", "no such context pack") from None
    if body.domain != stored.pack.domain:
        raise CortexApiError(422, "domain_mismatch", f"the pack is {stored.pack.domain!r}")
    if writeback.request_hash(body.case) != stored.pack.case_hash:
        raise CortexApiError(422, "case_mismatch", "this is not the case the pack was served for")
    authorise(request, caller, purpose=stored.pack.purpose, scope=stored.pack.scope)
    gate: PolicyGate = request.app.state.policy_gate
    permit = gate.approval(
        {
            "tenant_id": caller.tenant_id,
            "action": "propose",
            "actor": {"type": "consumer", "id": caller.consumer_id},
            "record": {"scope": stored.pack.scope, "contributors": []},
            "now": now_rfc3339(),
        }
    )
    if not permit.allow:
        raise CortexApiError(
            403, "access_denied", permit.reason, policy_revision=permit.policy_revision
        )
    pack = get_pack(stored.pack.domain)
    try:
        facts = case_facts(pack, body.case)
        outcome = writeback.submit(
            session,
            stored=stored,
            pack=pack,
            facts=facts,
            case=body.case,
            case_ref=body.case_id,
            corrector=str(caller.end_user_id),
            raw_correction=body.correction,
            idempotency_key=body.idempotency_key,
            payload_hash=payload_hash,
            policy=request.app.state.rule_suggestion,
        )
    except writeback.InvalidCorrectionError as exc:
        raise CortexApiError(422, "invalid_correction", str(exc)) from None
    except (KeyError, TypeError, ValueError) as exc:
        raise CortexApiError(422, "invalid_case", f"{type(exc).__name__}: {exc}") from None
    return _accepted(outcome, request.app.state.rule_suggestion)


def _accepted(
    outcome: writeback.Outcome, policy: writeback.RuleSuggestionPolicy | None
) -> CorrectionAccepted:
    row = outcome.correction
    suggestion = None
    if outcome.evidence is not None and policy is not None:
        suggestion = RuleSuggestion(
            met=outcome.evidence.met,
            distinct_cases=outcome.evidence.distinct_cases,
            distinct_correctors=outcome.evidence.distinct_correctors,
            min_distinct_cases=policy.min_distinct_cases,
            min_distinct_correctors=policy.min_distinct_correctors,
            window_days=policy.window_days,
        )
    return CorrectionAccepted(
        correction_id=uuid.UUID(row.correction_id),
        decision_record_id=uuid.UUID(row.decision_record_id),
        proposed_record_id=uuid.UUID(row.proposed_record_id) if row.proposed_record_id else None,
        review_case_id=uuid.UUID(row.review_case_id) if row.review_case_id else None,
        replayed=outcome.replayed,
        suggested_by=outcome.suggested_by,  # type: ignore[arg-type]
        rule_suggestion=suggestion,
        proposal_problems=admission.problems_payload(outcome.problems),
    )
