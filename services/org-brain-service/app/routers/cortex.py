"""/v1/cortex — the consumer API (D15), generated into libraries/cortex-contract.

Same prefix and authentication shape as integrations-service's /v1, so moving
these routes there later is a change of host, not of contract. The three broad
operations an agent needs are marked `x-mcp-tool`.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import json
import uuid
from typing import Any, cast

import structlog
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import UUID, literal, select, text, tuple_
from sqlalchemy import cast as cast_
from sqlalchemy.orm import Session

from app.api.errors import ERROR_RESPONSES, ApiError, CortexApiError
from app.config import OrgBrainConfig
from app.domain_packs.base import UnknownPackError, get_pack
from app.identity import (
    OUTCOMES_SCOPE,
    PACK_SCOPE,
    RULES_SYNC_SCOPE,
    AuthedCaller,
    caller_session,
    require_scope,
)
from app.models import Kind, MemoryRecord, Status
from app.schemas.cortex import (
    ContextPack,
    ContextPackRequest,
    CorrectionAccepted,
    CorrectionRequest,
    EvidenceExportView,
    ExpiringPage,
    ExpiringRecord,
    HoldReason,
    ImportItemResult,
    ImportRequest,
    ImportResult,
    ProposalAccepted,
    ProposeRecordRequest,
    RecordPage,
    RecordRead,
    RecordSummary,
    RenewItemResult,
    RenewRequest,
    RenewResult,
    RetireRequest,
    RetireResult,
    RuleAlert,
    RuleAlertItem,
    RuleAlertPage,
    RuleSuggestion,
    RuleSyncPage,
    SuggestionMetrics,
    SyncedRule,
    VerifyRequest,
    VerifyResult,
    WhoAmI,
)
from app.services import admission, evidence, ledger, replication, review, writeback
from app.services.access_gate import context_pack, verify
from app.services.access_gate.applicability import case_facts
from app.services.policy_gate import PolicyGate, now_rfc3339
from app.utils.business_dates import JST, business_start

_logger = structlog.get_logger("org-brain.cortex")
router = APIRouter(prefix="/v1/cortex", tags=["cortex"], responses=ERROR_RESPONSES)

SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
MCP_TOOLS = ("request_context_pack", "verify_output", "submit_correction", "report_outcome")


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
def whoami(caller: AuthedCaller = Depends(require_scope("cortex:read", PACK_SCOPE))) -> WhoAmI:
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


def _text_of(session: Session, record_id: str) -> str | None:
    body = review.body_or_none(session, ledger.latest(session, record_id))
    return str(body["text_ja"])[:200] if body and body.get("text_ja") else None


@router.get("/records/expiring", response_model=ExpiringPage, operation_id="list_expiring_records")
def list_expiring_records(
    request: Request,
    scope: uuid.UUID = Query(description="Scope node to read at; resolves upward"),
    purpose: str = Query(min_length=1),
    within_days: int = Query(default=30, ge=0, le=3650),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> ExpiringPage:
    """Basic expiry (issue 33): approved records whose validity ends within
    `within_days`, or has already ended, soonest first. Expiry itself is the
    record's `valid_to`, applied by business date when packs are built and
    verified; this list is for the owners to act before it happens."""
    decision = authorise(request, caller, purpose=purpose, scope=str(scope))
    allowed = [
        c
        for c, rank in SENSITIVITY_RANK.items()
        if rank <= SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    ]
    rows = (
        session.execute(
            text(
                "SELECT record_id::text AS record_id, version, kind, status, scope::text AS scope, "
                "enforcement, authority, sensitivity_class, valid_to, valid_to <= now() AS expired "
                "FROM memory_record_current WHERE status = 'approved' AND valid_to IS NOT NULL "
                "AND valid_to <= now() + make_interval(days => :days) "
                "AND scope = ANY(CAST(:scopes AS uuid[])) "
                "AND sensitivity_class = ANY(CAST(:allowed AS text[])) ORDER BY valid_to, record_id"
            ),
            {"days": within_days, "scopes": decision.readable_scopes, "allowed": allowed},
        )
        .mappings()
        .all()
    )
    return ExpiringPage(
        items=[
            ExpiringRecord.model_validate({**r, "title": _text_of(session, str(r["record_id"]))})
            for r in rows
        ],
        within_days=within_days,
        policy_revision=decision.policy_revision,
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


def _asserted_by(caller: AuthedCaller) -> dict[str, Any]:
    """A person behind the call asserts it; an agent alone asserts as a model."""
    if caller.end_user_id:
        return {"type": "user", "id": caller.end_user_id}
    return {"type": "model", "id": caller.consumer_id}


def _propose(
    body: ProposeRecordRequest,
    request: Request,
    caller: AuthedCaller,
    session: Session,
    asserted_by_override: dict[str, Any] | None = None,
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
        supersedes=str(body.supersedes) if body.supersedes else None,
    )
    _, problems = admission.validate_proposal(proposal)
    problems += admission.supersede_problems(session, proposal)
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
    asserted_by = asserted_by_override or _asserted_by(caller)
    # Scopes only tighten (issue 31): a machine may not loosen an inherited
    # constraint; a person may propose it, and the owner sees the warning.
    loosens = admission.inherited_conflicts(session, proposal, read.readable_scopes)
    if loosens and asserted_by["type"] != "user":
        raise CortexApiError(422, "invalid_proposal", admission.problems_payload(loosens))
    if loosens:
        proposal = dataclasses.replace(
            proposal,
            source={**proposal.source, "inherited_conflicts": admission.problems_payload(loosens)},
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
        warnings=admission.problems_payload(loosens),
    )


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
    route to the review queue (one open case per conflict). Never approved here.
    The same rule already waiting for a decision is returned, not queued twice."""
    waiting = admission.pending_same_content(
        session,
        body.schema_version,
        str(body.scope),
        body.entities,
        body.body,
        _business_start(body.valid_from) or dt.datetime.now(JST),
        _business_start(body.valid_to),
        asserted_by=_asserted_by(caller),
        supersedes=str(body.supersedes) if body.supersedes else None,
    )
    if waiting:
        authorise(request, caller, purpose=body.purpose, scope=str(body.scope))  # still a read
        row = ledger.latest(session, waiting[0])
        return ProposalAccepted(
            record_id=uuid.UUID(waiting[0]),
            version=row.version,
            status="proposed",
            review_case_id=uuid.UUID(waiting[1]),
            grouped=True,
            warnings=list((row.source or {}).get("inherited_conflicts") or []),
            already_pending=True,
        )
    return _propose(body, request, caller, session)


def _problem_list(code: str, detail: Any) -> list[dict[str, str]]:
    if not isinstance(detail, list):
        return [{"code": code, "detail": str(detail)}]
    out: list[dict[str, str]] = []
    for item in cast(list[Any], detail):
        if isinstance(item, dict):
            out.append({str(k): str(v) for k, v in cast(dict[Any, Any], item).items()})
    return out


@router.post(
    "/imports",
    response_model=ImportResult,
    operation_id="import_records",
)
def import_records(
    body: ImportRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> ImportResult:
    """A batch import (e.g. a source system's rule list) through the admission
    gate, item by item (issue 37, scenario 1). An import never approves:
    every new item is `proposed` (authority capped at 10 until an owner sets
    it), an item identical to an approved record is a `duplicate` (nothing
    written, that approval neither renewed nor extended), an item already
    past its validity is flagged `expired`, and a refused item does not stop
    the others."""
    if caller.end_user_id is None:
        raise CortexApiError(
            422, "importer_required", "an import needs the person running it as X-End-User-Id"
        )
    importer = {"type": "import", "id": body.source_system, "by": str(caller.end_user_id)}
    today = dt.datetime.now(JST)
    results: list[ImportItemResult] = []
    for index, item in enumerate(body.items):
        item = item.model_copy(
            update={
                "authority": min(item.authority, ledger.MODEL_AUTHORITY_CEILING),
                "source": {
                    **item.source,
                    "import": body.source_system,
                    "requested_authority": item.authority,
                },
            }
        )
        same = admission.approved_same_content(
            session,
            item.schema_version,
            str(item.scope),
            item.entities,
            item.body,
            _business_start(item.valid_from) or today,
            _business_start(item.valid_to),
        )
        if same and same.same_validity:
            results.append(
                ImportItemResult(
                    index=index, status="duplicate", record_id=uuid.UUID(same.record_id)
                )
            )
            continue
        waiting = admission.pending_same_content(
            session,
            item.schema_version,
            str(item.scope),
            item.entities,
            item.body,
            _business_start(item.valid_from) or today,
            _business_start(item.valid_to),
            asserted_by=importer,
            supersedes=str(item.supersedes) if item.supersedes else None,
        )
        if waiting:
            results.append(
                ImportItemResult(
                    index=index,
                    status="pending",
                    record_id=uuid.UUID(waiting[0]),
                    review_case_id=uuid.UUID(waiting[1]),
                )
            )
            continue
        if same and item.supersedes is None:
            # Same rule, other validity: a proposed change that replaces the
            # approved record only once an owner approves it.
            item = item.model_copy(update={"supersedes": uuid.UUID(same.record_id)})
        try:
            with session.begin_nested():
                accepted = _propose(item, request, caller, session, asserted_by_override=importer)
        except CortexApiError as exc:
            detail: Any = exc.body.get("detail")
            code = str(exc.body.get("error"))
            problems = _problem_list(code, detail)
            results.append(
                ImportItemResult(index=index, status="rejected", error=code, problems=problems)
            )
            continue
        expired = item.valid_to is not None and business_start(item.valid_to) <= today
        results.append(
            ImportItemResult(
                index=index,
                status="proposed",
                record_id=accepted.record_id,
                review_case_id=accepted.review_case_id,
                expired=expired,
                supersedes=item.supersedes,
            )
        )
    return ImportResult(source_system=body.source_system, items=results)


@router.post(
    "/records/{record_id}/retire",
    response_model=RetireResult,
    operation_id="retire_record",
    responses={
        404: {"model": ApiError, "description": "No such record (or another tenant's)"},
        409: {
            "model": ApiError,
            "description": "Not approved: nothing to retire; or a replica (`replica_read_only`)",
        },
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
    if replication.is_replica(current):
        home = current.asserted_by.get("id", "its home region")
        raise CortexApiError(
            409, "replica_read_only", f"a replicated Global record is retired in {home}"
        )
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
            "record": {
                "scope": current.scope,
                "contributors": [],
                "entities": list(current.entities or []),
            },
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
    description="Scopes: `cortex:read`, or `cortex:pack` (the Phase 1 integration profile: "
    "a consumer that may only get context packs).",
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
    caller: AuthedCaller = Depends(require_scope("cortex:read", PACK_SCOPE)),
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
    description="Scopes: `cortex:read`, or `cortex:pack` (the Phase 1 integration profile).",
    responses={404: {"model": ApiError, "description": "No such pack (or another tenant's)"}},
)
def get_context_pack(
    pack_id: uuid.UUID,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:read", PACK_SCOPE)),
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
    read = authorise(request, caller, purpose=stored.pack.purpose, scope=stored.pack.scope)
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
            policy=_suggestion_policy(request, pack.name),
            ancestors=read.readable_scopes,  # own scope too (issue 50)
        )
    except writeback.InvalidCorrectionError as exc:
        raise CortexApiError(422, "invalid_correction", str(exc)) from None
    except (KeyError, TypeError, ValueError) as exc:
        raise CortexApiError(422, "invalid_case", f"{type(exc).__name__}: {exc}") from None
    return _accepted(outcome, _suggestion_policy(request, body.domain))


def _suggestion_policy(request: Request, domain: str) -> writeback.RuleSuggestionPolicy:
    """The D17 thresholds for this domain (issue 46), else the default."""
    by_domain: dict[str, writeback.RuleSuggestionPolicy] = getattr(
        request.app.state, "rule_suggestion_by_domain", {}
    )
    default: writeback.RuleSuggestionPolicy = request.app.state.rule_suggestion
    return by_domain.get(domain, default)


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


@router.post(
    "/outcomes",
    response_model=CorrectionAccepted,
    status_code=202,
    operation_id="report_outcome",
    responses={
        404: {"model": ApiError, "description": "No such pack (or another tenant's)"},
        409: {"model": ApiError, "description": "Idempotency key reused for another request"},
    },
    openapi_extra=_mcp(
        "report_outcome",
        "Report how a payment was finally resolved (confirmed or corrected), who decided and "
        "why, with the pack id. Cortex remembers it as a past case and may suggest a rule.",
    ),
)
def report_outcome(
    body: CorrectionRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write", OUTCOMES_SCOPE)),
    session: Session = Depends(caller_session),
) -> CorrectionAccepted:
    """IBM's third operation (issue 43, slide 20): the outcome of a reviewed
    case. The same writeback as a correction, under a scope that allows
    nothing else; the person who decided is X-End-User-Id."""
    return submit_correction(body, request, caller, session)


_REMOVED = ("deprecated", "superseded", "stale", "erased")


def _encode_cursor(row: MemoryRecord) -> str:
    """Opaque: where the last page ended, as (recorded_at, record_id, version).
    No numeric key leaves the service (house rule)."""
    raw = json.dumps([row.recorded_at.isoformat(), row.record_id, row.version])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[dt.datetime, str, int] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, record_id, version = json.loads(raw)
        return dt.datetime.fromisoformat(at), str(uuid.UUID(record_id)), int(version)
    except (ValueError, TypeError, UnicodeDecodeError):
        raise CortexApiError(422, "invalid_cursor", "pass back a next_cursor as given") from None


@router.get(
    "/rules/sync",
    response_model=RuleSyncPage,
    operation_id="sync_rules",
    description="For a rule engine (issue 44): the domain's rules a scope sees, as changes "
    "since `cursor`. `upsert` an approved version; `remove` one no longer in force. Start "
    "without a cursor for everything; keep `next_cursor` for the next call (daily or on change).",
)
def sync_rules(
    request: Request,
    domain: str = Query(min_length=1, max_length=64),
    scope: uuid.UUID = Query(),
    purpose: str = Query(min_length=1),
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=200, ge=1, le=1000),
    caller: AuthedCaller = Depends(require_scope("cortex:read", RULES_SYNC_SCOPE)),
    session: Session = Depends(caller_session),
) -> RuleSyncPage:
    after = _decode_cursor(cursor)
    decision = authorise(request, caller, purpose=purpose, scope=str(scope))
    allowed = [
        c
        for c, rank in SENSITIVITY_RANK.items()
        if rank <= SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    ]
    query = (
        select(MemoryRecord)
        .where(
            MemoryRecord.kind == "rule",
            MemoryRecord.schema_version == f"{domain}.rule/1",
            MemoryRecord.scope.in_(decision.readable_scopes),
            MemoryRecord.sensitivity_class.in_(allowed),
        )
        .order_by(MemoryRecord.recorded_at, MemoryRecord.record_id, MemoryRecord.version)
        .limit(limit + 1)
    )
    if after is not None:
        query = query.where(
            tuple_(MemoryRecord.recorded_at, MemoryRecord.record_id, MemoryRecord.version)
            > tuple_(literal(after[0]), cast_(literal(after[1]), UUID), literal(after[2]))
        )
    versions = list(session.scalars(query))
    has_more = len(versions) > limit
    page = versions[:limit]
    latest: dict[str, MemoryRecord] = {}
    for row in page:
        latest[row.record_id] = row  # the page's last state of each record
    items: list[SyncedRule] = []
    for row in latest.values():
        if row.status == Status.APPROVED.value:
            user = (row.approved_by or {}).get("user_id")
            items.append(
                SyncedRule(
                    record_id=uuid.UUID(row.record_id),
                    version=row.version,
                    change="upsert",
                    status=row.status,
                    enforcement=row.enforcement,
                    authority=row.authority,
                    scope=uuid.UUID(row.scope),
                    entities=list(row.entities or []),
                    valid_from=row.valid_from,
                    valid_to=row.valid_to,
                    approved_by=uuid.UUID(str(user)) if user else None,
                    body=review.body_or_none(session, row),
                )
            )
        elif row.status in _REMOVED:
            items.append(
                SyncedRule(
                    record_id=uuid.UUID(row.record_id),
                    version=row.version,
                    change="remove",
                    status=row.status,
                    scope=uuid.UUID(row.scope),
                )
            )
        # proposed: not a rule yet, nothing to sync
    return RuleSyncPage(
        domain=domain,
        items=items,
        next_cursor=_encode_cursor(page[-1]) if page else (cursor or ""),
        has_more=has_more,
        policy_revision=decision.policy_revision,
    )


_ALERT_WINDOW_DAYS = 90
_ALERT_MIN_COUNT = 3


@router.get(
    "/rules/alerts",
    response_model=RuleAlertPage,
    operation_id="list_rule_alerts",
    description="For owners (issue 48, slide 18): approved rules that need a look. review_due: "
    "not reviewed for a year; expiring: valid_to within `within_days`; often_overridden: "
    "specialists corrected cases where it applied, 3+ times in 90 days; keeps_losing: served "
    "but overridden by another rule, 3+ times in 90 days.",
)
def list_rule_alerts(
    request: Request,
    scope: uuid.UUID = Query(),
    purpose: str = Query(min_length=1),
    within_days: int = Query(default=90, ge=1, le=366),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> RuleAlertPage:
    decision = authorise(request, caller, purpose=purpose, scope=str(scope))
    allowed = [
        c
        for c, rank in SENSITIVITY_RANK.items()
        if rank <= SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    ]
    review_every = OrgBrainConfig().review_every_days
    rows = (
        session.execute(
            text(
                "WITH r AS (SELECT record_id, version, scope, entities, valid_to, recorded_at "
                "  FROM memory_record_current WHERE kind = 'rule' AND status = 'approved' "
                "  AND scope = ANY(CAST(:scopes AS uuid[])) "
                "  AND sensitivity_class = ANY(CAST(:allowed AS text[]))), "
                "o AS (SELECT i.record_id, count(DISTINCT c.case_ref) AS n FROM correction c "
                "  JOIN served_pack_item i ON i.pack_id = c.pack_id "
                "  AND i.role IN ('constraint', 'applied') "
                "  WHERE c.conflict_key NOT LIKE 'confirmed:%' "
                "  AND c.created_at >= now() - make_interval(days => :window) "
                "  GROUP BY i.record_id), "
                "l AS (SELECT i.record_id, count(*) AS n FROM served_pack_item i "
                "  JOIN served_pack p ON p.pack_id = i.pack_id WHERE i.role = 'overridden' "
                "  AND p.served_at >= now() - make_interval(days => :window) "
                "  GROUP BY i.record_id) "
                "SELECT r.record_id::text AS record_id, r.version, r.scope::text AS scope, "
                "  r.entities, "
                "  r.valid_to, "
                "  r.recorded_at < now() - make_interval(days => :review) AS review_due, "
                "  r.valid_to IS NOT NULL AND r.valid_to <= now() + make_interval(days => :days) "
                "    AS expiring, "
                "  coalesce(o.n, 0) AS overrides, coalesce(l.n, 0) AS losses "
                "FROM r LEFT JOIN o ON o.record_id = r.record_id "
                "LEFT JOIN l ON l.record_id = r.record_id ORDER BY r.record_id"
            ),
            {
                "scopes": decision.readable_scopes,
                "allowed": allowed,
                "window": _ALERT_WINDOW_DAYS,
                "review": review_every,
                "days": within_days,
            },
        )
        .mappings()
        .all()
    )
    items: list[RuleAlert] = []
    for r in rows:
        alerts: list[RuleAlertItem] = []
        if r["review_due"]:
            alerts.append(
                RuleAlertItem(kind="review_due", detail=f"not reviewed for {review_every} days")
            )
        if r["expiring"]:
            alerts.append(RuleAlertItem(kind="expiring", detail=f"ends within {within_days} days"))
        if r["overrides"] >= _ALERT_MIN_COUNT:
            alerts.append(
                RuleAlertItem(
                    kind="often_overridden",
                    count=int(r["overrides"]),
                    detail=f"corrected in {r['overrides']} cases where it applied (90 days)",
                )
            )
        if r["losses"] >= _ALERT_MIN_COUNT:
            alerts.append(
                RuleAlertItem(
                    kind="keeps_losing",
                    count=int(r["losses"]),
                    detail=f"overridden by another rule in {r['losses']} packs (90 days)",
                )
            )
        if alerts:
            items.append(
                RuleAlert(
                    record_id=uuid.UUID(r["record_id"]),
                    version=r["version"],
                    title=_text_of(session, r["record_id"]),
                    scope=uuid.UUID(r["scope"]),
                    entities=list(r["entities"] or []),
                    valid_to=r["valid_to"],
                    alerts=alerts,
                )
            )
    return RuleAlertPage(items=items, policy_revision=decision.policy_revision)


@router.post(
    "/rules/renew",
    response_model=RenewResult,
    operation_id="renew_rules",
    description="April (issue 48): propose a new version of each rule with a new end date. "
    "Each goes through the review queue as a supersede; nothing changes until its owner approves.",
)
def renew_rules(
    body: RenewRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> RenewResult:
    if caller.end_user_id is None:
        raise CortexApiError(
            422, "person_required", "a renewal is proposed by a person (X-End-User-Id)"
        )
    results: list[RenewItemResult] = []
    for record_id in body.record_ids:
        try:
            current = ledger.latest(session, str(record_id))
        except ledger.RecordNotFoundError:
            results.append(
                RenewItemResult(
                    record_id=record_id,
                    status="rejected",
                    problems=[{"code": "not_found", "detail": "no such record"}],
                )
            )
            continue
        rule_body = review.body_or_none(session, current)
        if current.status != Status.APPROVED.value or current.kind != "rule" or rule_body is None:
            results.append(
                RenewItemResult(
                    record_id=record_id,
                    status="rejected",
                    problems=[
                        {
                            "code": "not_renewable",
                            "detail": f"a {current.kind} that is {current.status}",
                        }
                    ],
                )
            )
            continue
        request_item = ProposeRecordRequest(
            domain=current.schema_version.split(".", 1)[0],
            kind="rule",
            schema_version=current.schema_version,
            body=rule_body,
            scope=uuid.UUID(current.scope),
            entities=list(current.entities or []),
            enforcement=current.enforcement,  # type: ignore[arg-type]
            authority=min(current.authority, 40),
            sensitivity=current.sensitivity,  # type: ignore[arg-type]
            purpose=body.purpose,
            valid_from=(current.valid_from + dt.timedelta(hours=9)).date(),
            valid_to=body.valid_to,
            source={"renewal_of": str(record_id)},
            supersedes=record_id,
        )
        try:
            with session.begin_nested():
                accepted = propose_record(request_item, request, caller, session)
        except CortexApiError as exc:
            results.append(
                RenewItemResult(
                    record_id=record_id,
                    status="rejected",
                    problems=_problem_list(str(exc.body.get("error")), exc.body.get("detail")),
                )
            )
            continue
        results.append(
            RenewItemResult(
                record_id=record_id,
                status="pending" if accepted.already_pending else "proposed",
                proposed_record_id=accepted.record_id,
                review_case_id=accepted.review_case_id,
            )
        )
    return RenewResult(items=results)


@router.get(
    "/metrics/suggestions",
    response_model=SuggestionMetrics,
    operation_id="suggestion_metrics",
    description="Slide 26 (issue 49): of the rules Cortex suggested (asserted by a model or a "
    "policy), how many owners accepted, rejected, or still have to decide, in this scope.",
)
def suggestion_metrics(
    request: Request,
    scope: uuid.UUID = Query(),
    purpose: str = Query(min_length=1),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> SuggestionMetrics:
    decision = authorise(request, caller, purpose=purpose, scope=str(scope))
    rows = session.execute(
        text(
            "SELECT record_id::text, status FROM memory_record_current WHERE kind = 'rule' "
            "AND asserted_by->>'type' IN ('model', 'policy') "
            "AND scope = ANY(CAST(:scopes AS uuid[]))"
        ),
        {"scopes": decision.readable_scopes},
    ).all()
    approved_ever = review.ever_approved(session, [r[0] for r in rows])
    accepted = sum(1 for r in rows if r[0] in approved_ever)
    pending = sum(1 for r in rows if r[1] == "proposed")
    rejected = len(rows) - accepted - pending
    decided = accepted + rejected
    return SuggestionMetrics(
        suggested=len(rows),
        accepted=accepted,
        rejected=rejected,
        pending=pending,
        acceptance=round(accepted / decided, 3) if decided else None,
        policy_revision=decision.policy_revision,
    )
