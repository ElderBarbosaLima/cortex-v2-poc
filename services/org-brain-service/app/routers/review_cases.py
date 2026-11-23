"""/v1/cortex/review-cases — the review queue (issue 18; the Decision Desk's API).

Reading a case is a read (OPA read decision for the purpose, sensitivity
ceiling). Deciding is OPA's approval decision: a person with memory.approve at
the case's scope, not a contributor, the named owner or backup. Every decision
fails closed: OPA down is a 503 and nothing is written.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ERROR_RESPONSES, ApiError, CortexApiError
from app.identity import AuthedCaller, caller_session, require_scope
from app.routers.cortex import authorise
from app.schemas.cortex import (
    ApproveRequest,
    CanDecide,
    CaseProposal,
    DecidedRecord,
    DecisionPermission,
    DecisionResult,
    DecisionView,
    NamedOwners,
    RejectRequest,
    ReviewCaseDetail,
    ReviewCasePage,
    ReviewCaseSummary,
)
from app.seed.tenants import Seed, named_owners
from app.services import ledger, review
from app.services.admission import SENSITIVITY_RANK
from app.services.policy_gate import PolicyGate, now_rfc3339

router = APIRouter(prefix="/v1/cortex/review-cases", tags=["review"], responses=ERROR_RESPONSES)

_CASE_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ApiError, "description": "No such case (or another tenant's)"},
}
_DECISION_ERRORS: dict[int | str, dict[str, Any]] = {
    **_CASE_ERRORS,
    409: {"model": ApiError, "description": "The case is already decided"},
}


def _owners(request: Request, caller: AuthedCaller, scope: str) -> NamedOwners | None:
    seed: Seed = request.app.state.seed
    tenant = next(t for t in seed.tenants if t.tenant_id == caller.tenant_id)
    found = named_owners(tenant, scope)
    return NamedOwners.model_validate(found) if found else None


def _ceiling(decision: Any) -> int:
    return SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)


_RANK_SQL = " ".join(f"WHEN '{c}' THEN {r}" for c, r in SENSITIVITY_RANK.items())


@router.get("", response_model=ReviewCasePage, operation_id="list_review_cases")
def list_review_cases(
    request: Request,
    scope: uuid.UUID = Query(description="Scope node to read at; resolves upward"),
    purpose: str = Query(min_length=1),
    status: str = Query(default="open", pattern="^(open|decided)$"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: uuid.UUID | None = Query(default=None),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> ReviewCasePage:
    """Cases in the scopes OPA lets this purpose read, oldest first, only those
    whose most sensitive proposal is within the purpose's ceiling."""
    decision = authorise(request, caller, purpose=purpose, scope=str(scope))
    rows = session.execute(
        text(
            "SELECT c.case_id::text AS case_id, c.scope::text AS scope, c.status, c.opened_at, "
            "count(*) AS n, max(CASE r.sensitivity_class " + _RANK_SQL + " END) AS rank, "
            "(array_agg(i.record_id::text ORDER BY i.id))[1] AS first_record "
            "FROM review_case c JOIN review_case_item i ON i.case_id = c.case_id "
            "JOIN memory_record_current r ON r.record_id = i.record_id "
            "WHERE c.scope = ANY(CAST(:scopes AS uuid[])) AND c.status = :status "
            "AND (CAST(:cursor AS uuid) IS NULL OR c.case_id > CAST(:cursor AS uuid)) "
            "GROUP BY c.case_id, c.scope, c.status, c.opened_at "
            "HAVING max(CASE r.sensitivity_class " + _RANK_SQL + " END) <= :ceiling "
            "ORDER BY c.case_id LIMIT :limit"
        ),
        {
            "scopes": decision.readable_scopes,
            "status": status,
            "cursor": str(cursor) if cursor else None,
            "ceiling": _ceiling(decision),
            "limit": limit + 1,
        },
    ).all()
    by_rank = {r: c for c, r in SENSITIVITY_RANK.items()}
    items = [
        ReviewCaseSummary(
            case_id=uuid.UUID(r.case_id),
            scope=uuid.UUID(r.scope),
            title=review.title(session, ledger.latest(session, r.first_record)),
            status=r.status,
            opened_at=r.opened_at,
            proposal_count=r.n,
            sensitivity_class=by_rank[r.rank],  # type: ignore[arg-type]
            named_owners=_owners(request, caller, r.scope),
        )
        for r in rows[:limit]
    ]
    next_cursor = str(items[-1].case_id) if len(rows) > limit else None
    return ReviewCasePage(
        items=items, next_cursor=next_cursor, policy_revision=decision.policy_revision
    )


def _load(session: Session, case_id: uuid.UUID, *, for_update: bool = False) -> review.OpenCase:
    try:
        return review.load_case(session, str(case_id), for_update=for_update)
    except review.CaseNotFoundError:
        raise CortexApiError(404, "not_found", "no such review case") from None


def _affected(oc: review.OpenCase) -> list[str]:
    refs: set[str] = set()
    for row in oc.items:
        if row.source.get("case_ref"):
            refs.add(str(row.source["case_ref"]))
        refs.update(str(c) for c in row.source.get("case_refs", []))
    return sorted(refs)


@router.get(
    "/{case_id}",
    response_model=ReviewCaseDetail,
    operation_id="get_review_case",
    responses=_CASE_ERRORS,
)
def get_review_case(
    case_id: uuid.UUID,
    request: Request,
    purpose: str = Query(min_length=1),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> ReviewCaseDetail:
    """The proposals (bodies opened), their evidence, the work cases they
    affect, the people behind them and every decision so far."""
    oc = _load(session, case_id)
    decision = authorise(request, caller, purpose=purpose, scope=oc.case.scope)
    if SENSITIVITY_RANK[oc.sensitivity_class] > _ceiling(decision):
        raise CortexApiError(
            403,
            "access_denied",
            f"sensitivity {oc.sensitivity_class} is above this purpose's ceiling",
            policy_revision=decision.policy_revision,
        )
    approved = review.ever_approved(session, [r.record_id for r in oc.items])
    proposals = [
        CaseProposal.model_validate(
            {
                **{c: getattr(row, c) for c in CaseProposal.model_fields if hasattr(row, c)},
                "body": (body := review.body_or_none(session, row)),
                "body_erased": body is None,
                "outcome": review.outcome(row.status, row.record_id in approved),
            }
        )
        for row in oc.items
    ]
    return ReviewCaseDetail(
        case_id=uuid.UUID(oc.case.case_id),
        scope=uuid.UUID(oc.case.scope),
        title=review.title(session, oc.items[0]),
        status=oc.case.status,  # type: ignore[arg-type]
        opened_at=oc.case.opened_at,
        proposal_count=len(oc.items),
        sensitivity_class=oc.sensitivity_class,  # type: ignore[arg-type]
        named_owners=_owners(request, caller, oc.case.scope),
        proposals=proposals,
        can_decide=_can_decide(request, caller, oc),
        affected_cases=_affected(oc),
        contributors=[uuid.UUID(c) for c in oc.contributors],
        decisions=[
            DecisionView.model_validate(d, from_attributes=True)
            for d in review.decisions(session, oc.case.case_id)
        ],
        policy_revision=decision.policy_revision,
    )


def _ask(request: Request, caller: AuthedCaller, action: str, oc: review.OpenCase) -> Any:
    actor = (
        {"type": "user", "id": caller.end_user_id}
        if caller.end_user_id
        else {"type": "consumer", "id": caller.consumer_id}
    )
    gate: PolicyGate = request.app.state.policy_gate
    return gate.approval(
        {
            "tenant_id": caller.tenant_id,
            "action": action,
            "actor": actor,
            "record": {"scope": oc.case.scope, "contributors": oc.contributors},
            "now": now_rfc3339(),
        }
    )


def _can_decide(request: Request, caller: AuthedCaller, oc: review.OpenCase) -> CanDecide:
    def one(action: str) -> DecisionPermission:
        if oc.case.status != "open" or not oc.pending:
            return DecisionPermission(
                allowed=False, reason="this case is already decided", policy_revision=None
            )
        d = _ask(request, caller, action, oc)
        return DecisionPermission(
            allowed=d.allow, reason=d.reason, policy_revision=d.policy_revision
        )

    return CanDecide(approve=one("approve"), reject=one("reject"))


def _permit(request: Request, caller: AuthedCaller, action: str, oc: review.OpenCase) -> Any:
    permit = _ask(request, caller, action, oc)
    if not permit.allow:
        raise CortexApiError(
            403, "access_denied", permit.reason, policy_revision=permit.policy_revision
        )
    return permit


def _result(session: Session, decided: review.Decided, case_id: str) -> DecisionResult:
    approved = review.ever_approved(session, [v.record_id for v in decided.versions])
    return DecisionResult(
        decision_id=uuid.UUID(decided.decision.decision_id),
        case_id=uuid.UUID(case_id),
        decision=decided.decision.decision,  # type: ignore[arg-type]
        records=[
            DecidedRecord(
                record_id=uuid.UUID(v.record_id),
                version=v.version,
                status=v.status,  # type: ignore[arg-type]
                outcome=review.outcome(v.status, v.record_id in approved),
            )
            for v in decided.versions
        ],
        policy_revision=decided.decision.policy_revision,
    )


@router.post(
    "/{case_id}/approve",
    response_model=DecisionResult,
    operation_id="approve_review_case",
    responses=_DECISION_ERRORS,
)
def approve_review_case(
    case_id: uuid.UUID,
    body: ApproveRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> DecisionResult:
    """The named owner (or backup) approves one proposal, setting its final
    authority and enforcement. Needs memory.approve and separation of duties."""
    oc = _load(session, case_id, for_update=True)
    permit = _permit(request, caller, "approve", oc)
    try:
        decided = review.approve(
            session,
            tenant_id=caller.tenant_id,
            oc=oc,
            user_id=str(caller.end_user_id),
            record_id=str(body.record_id) if body.record_id else None,
            authority=body.authority,
            enforcement=body.enforcement,
            reason=body.reason,
            policy_revision=permit.policy_revision,
        )
    except review.CaseDecidedError as exc:
        raise CortexApiError(409, "case_decided", str(exc)) from None
    except review.InvalidDecisionError as exc:
        raise CortexApiError(422, "invalid_decision", str(exc)) from None
    return _result(session, decided, oc.case.case_id)


@router.post(
    "/{case_id}/reject",
    response_model=DecisionResult,
    operation_id="reject_review_case",
    responses=_DECISION_ERRORS,
)
def reject_review_case(
    case_id: uuid.UUID,
    body: RejectRequest,
    request: Request,
    caller: AuthedCaller = Depends(require_scope("cortex:write")),
    session: Session = Depends(caller_session),
) -> DecisionResult:
    """The named owner (or backup) rejects the case: every pending proposal
    becomes deprecated without ever being approved (outcome: rejected)."""
    oc = _load(session, case_id, for_update=True)
    permit = _permit(request, caller, "reject", oc)
    try:
        decided = review.reject(
            session,
            tenant_id=caller.tenant_id,
            oc=oc,
            user_id=str(caller.end_user_id),
            reason=body.reason,
            policy_revision=permit.policy_revision,
        )
    except review.CaseDecidedError as exc:
        raise CortexApiError(409, "case_decided", str(exc)) from None
    return _result(session, decided, oc.case.case_id)
