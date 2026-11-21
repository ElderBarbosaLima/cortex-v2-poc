"""/v1/cortex — the consumer API (D15). Issue 13 adds identity and the first
authorised read; issue 16 turns this into the single contract."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.identity import AuthedCaller, caller_session, require_scope
from app.services import ledger
from app.services.policy_gate import PolicyGate, now_rfc3339

router = APIRouter(prefix="/v1/cortex", tags=["cortex"])

SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class WhoAmI(BaseModel):
    consumer_id: str
    tenant_id: str
    end_user_id: str | None
    scopes: list[str]


class RecordSummary(BaseModel):
    record_id: str
    version: int
    kind: str
    status: str
    scope: str
    enforcement: str | None
    authority: int
    sensitivity_class: str
    policy_revision: str | None


def _forbidden(reason: str, policy_revision: str | None) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"error": "access_denied", "detail": reason, "policy_revision": policy_revision},
    )


def read_payload(caller: AuthedCaller, *, purpose: str, scope_node: str) -> dict[str, Any]:
    return {
        "tenant_id": caller.tenant_id,
        "consumer": {"id": caller.consumer_id},
        "end_user": {"id": caller.end_user_id} if caller.end_user_id else None,
        "purpose": purpose,
        "scope_node": scope_node,
        "now": now_rfc3339(),
    }


@router.get("/whoami", response_model=WhoAmI)
def whoami(caller: AuthedCaller = Depends(require_scope("cortex:read"))) -> WhoAmI:
    return WhoAmI(**caller.model_dump())


@router.get("/records/{record_id}", response_model=RecordSummary)
def get_record(
    record_id: uuid.UUID,
    request: Request,
    purpose: str = Query(min_length=1),
    caller: AuthedCaller = Depends(require_scope("cortex:read")),
    session: Session = Depends(caller_session),
) -> RecordSummary:
    """One record's current version, if this caller may read it for `purpose`.

    Another tenant's record is invisible under RLS, so it is a 404, exactly
    like a record that does not exist: no existence oracle across tenants."""
    try:
        row = ledger.latest(session, str(record_id))
    except ledger.RecordNotFoundError:
        raise HTTPException(status_code=404, detail={"error": "not_found"}) from None
    gate: PolicyGate = request.app.state.policy_gate
    decision = gate.read(read_payload(caller, purpose=purpose, scope_node=row.scope))
    if not decision.allow:
        raise _forbidden(decision.reason, decision.policy_revision)
    ceiling = SENSITIVITY_RANK.get(decision.sensitivity_ceiling or "", -1)
    if SENSITIVITY_RANK[row.sensitivity_class] > ceiling:
        raise _forbidden(
            f"sensitivity {row.sensitivity_class} is above this purpose's ceiling",
            decision.policy_revision,
        )
    return RecordSummary(
        record_id=row.record_id,
        version=row.version,
        kind=row.kind,
        status=row.status,
        scope=row.scope,
        enforcement=row.enforcement,
        authority=row.authority,
        sensitivity_class=row.sensitivity_class,
        policy_revision=decision.policy_revision,
    )
