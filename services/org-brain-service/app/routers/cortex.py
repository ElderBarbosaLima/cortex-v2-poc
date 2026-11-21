"""/v1/cortex — the consumer API (D15), generated into libraries/cortex-contract.

Same prefix and authentication shape as integrations-service's /v1, so moving
these routes there later is a change of host, not of contract. The three broad
operations an agent needs are marked `x-mcp-tool`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.errors import ERROR_RESPONSES, ApiError, CortexApiError
from app.identity import AuthedCaller, caller_session, require_scope
from app.schemas.cortex import (
    ContextPack,
    ContextPackRequest,
    CorrectionAccepted,
    CorrectionRequest,
    RecordPage,
    RecordRead,
    RecordSummary,
    VerifyRequest,
    VerifyResult,
    WhoAmI,
)
from app.services import ledger
from app.services.policy_gate import PolicyGate, now_rfc3339

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


def _authorise(request: Request, caller: AuthedCaller, *, purpose: str, scope: str) -> Any:
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
    decision = _authorise(request, caller, purpose=purpose, scope=str(scope))
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
    decision = _authorise(request, caller, purpose=purpose, scope=row.scope)
    ceiling = SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    if SENSITIVITY_RANK[row.sensitivity_class] > ceiling:
        raise CortexApiError(
            403,
            "access_denied",
            f"sensitivity {row.sensitivity_class} is above this purpose's ceiling",
            policy_revision=decision.policy_revision,
        )
    return RecordRead(**_summary(row).model_dump(), policy_revision=decision.policy_revision)


def _not_yet(issue: int) -> CortexApiError:
    return CortexApiError(501, "not_implemented", f"arrives in issue {issue} (docs/tasks.md)")


@router.post(
    "/context-packs",
    response_model=ContextPack,
    status_code=201,
    operation_id="request_context_pack",
    responses={501: {"model": ApiError}},
    openapi_extra=_mcp(
        "request_context_pack",
        "Get the governed context for one case: every applicable mandatory rule and "
        "prohibition, relevant approved knowledge, stored as a pack with an id to verify against.",
    ),
)
def request_context_pack(
    body: ContextPackRequest, caller: AuthedCaller = Depends(require_scope("cortex:read"))
) -> ContextPack:
    raise _not_yet(20)


@router.post(
    "/verify",
    response_model=VerifyResult,
    operation_id="verify_output",
    responses={501: {"model": ApiError}},
    openapi_extra=_mcp(
        "verify_output",
        "Before posting, check an output against its pack: every mandatory rule still "
        "approved and current, no new one since. Answers pass or hold with reasons.",
    ),
)
def verify_output(
    body: VerifyRequest, caller: AuthedCaller = Depends(require_scope("cortex:read"))
) -> VerifyResult:
    raise _not_yet(21)


@router.post(
    "/corrections",
    response_model=CorrectionAccepted,
    status_code=202,
    operation_id="submit_correction",
    responses={501: {"model": ApiError}},
    openapi_extra=_mcp(
        "submit_correction",
        "Report a reviewer's correction of an output (with its pack id). Cortex records "
        "the decision and may propose a rule for a named owner to approve.",
    ),
)
def submit_correction(
    body: CorrectionRequest, caller: AuthedCaller = Depends(require_scope("cortex:write"))
) -> CorrectionAccepted:
    raise _not_yet(22)
