"""Verify: the release check before an agent posts (issue 21, Rev. B rule 5, D3).

Given a served pack and the output the agent is about to post, answer `pass`
or `hold` with reasons, in the shape of the UAT's Cortex Assurance result.
Every check reads the **ledger**, never the search projection, so a stale
index can neither hide a retired rule nor invent one:

1. every constraint (mandatory, prohibition) in the pack is still approved
   and valid at the pack's `as_of` → else `rule_no_longer_approved`;
2. applicability is re-run for the same case, as of the same business date,
   at the scopes OPA allows now → a constraint that applies now but was not
   in the pack is `new_mandatory_rule`; one that cannot be evaluated is
   `constraint_unevaluable`;
3. the domain pack checks the output against the rules it must satisfy
   (`DomainPack.check_output`, optional) → `output_check_failed`.
The result is stored, append-only, linked to the pack. Cortex never blocks
the client's system itself (D3); a `hold` is the client's signal to stop.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.domain_packs.base import DomainPack, RuleBody
from app.models import Status, Verification
from app.services import ledger
from app.services.access_gate.applicability import CaseFacts, applicable_rules
from app.services.access_gate.context_pack import Access, StoredPack
from app.services.body_codec import body_hash
from app.utils.business_dates import business_start
from app.utils.ids import new_record_id

CONSTRAINTS = frozenset({"mandatory", "prohibition"})


class CaseMismatchError(ValueError):
    """The case sent to verify is not the one the pack was served for."""


@dataclass(frozen=True)
class Hold:
    reason: str
    record_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Checked:
    verification: Verification
    holds: list[Hold] = field(default_factory=list[Hold])

    @property
    def result(self) -> Literal["pass", "hold"]:
        return "hold" if self.holds else "pass"


def _still_approved(session: Session, stored: StoredPack) -> list[Hold]:
    at = business_start(stored.pack.as_of)
    holds: list[Hold] = []
    for item, _ in stored.items:
        if item.enforcement not in CONSTRAINTS:
            continue
        current = ledger.latest(session, item.record_id)
        if current.status != Status.APPROVED.value:
            holds.append(Hold("rule_no_longer_approved", item.record_id, f"now {current.status}"))
        elif not ledger.is_valid_at(current, at):
            holds.append(
                Hold("rule_no_longer_approved", item.record_id, "not valid at the pack's as_of")
            )
    return holds


def _new_constraints(
    session: Session, stored: StoredPack, *, pack: DomainPack, facts: CaseFacts, access: Access
) -> list[Hold]:
    served = {item.record_id for item, _ in stored.items}
    now = applicable_rules(
        session,
        pack=pack,
        facts=facts,
        readable_scopes=access.readable_scopes,
        as_of=stored.pack.as_of,
    )
    holds = [
        Hold("new_mandatory_rule", r.record_id, f"{r.enforcement} approved after the pack")
        for r in now.rules
        if r.enforcement in CONSTRAINTS and r.record_id not in served
    ]
    holds += [
        Hold("constraint_unevaluable", u.record_id, f"{u.code}: {u.detail}")
        for u in now.unevaluable
        if u.enforcement in CONSTRAINTS
    ]
    return holds


def _output_problems(
    stored: StoredPack, *, pack: DomainPack, output: Mapping[str, Any]
) -> list[Hold]:
    rules: list[RuleBody] = []
    for item, body in stored.items:
        if body is not None and item.kind == "rule" and item.role in ("constraint", "applied"):
            try:
                rules.append(pack.rule_body_schema.model_validate(body))
            except ValidationError:
                continue  # a body the pack cannot read was already a constraint check above
    return [Hold("output_check_failed", detail=p) for p in pack.check_output(output, rules)]


def verify(
    session: Session,
    *,
    stored: StoredPack,
    pack: DomainPack,
    facts: CaseFacts,
    case: Mapping[str, Any],
    output: Mapping[str, Any],
    access: Access,
    trace_id: str,
    consumer_id: str,
    end_user_id: str | None,
) -> Checked:
    if body_hash(dict(case)) != stored.pack.case_hash:
        raise CaseMismatchError("this is not the case the pack was served for")
    holds = [
        *_still_approved(session, stored),
        *_new_constraints(session, stored, pack=pack, facts=facts, access=access),
        *_output_problems(stored, pack=pack, output=output),
    ]
    row = Verification(
        verification_id=new_record_id(),
        tenant_id=stored.pack.tenant_id,
        pack_id=stored.pack.pack_id,
        trace_id=trace_id,
        consumer_id=consumer_id,
        end_user_id=end_user_id,
        result="hold" if holds else "pass",
        reasons=[{"reason": h.reason, "record_id": h.record_id, "detail": h.detail} for h in holds],
        output_hash=body_hash(dict(output)),
        policy_revision=access.policy_revision,
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    return Checked(row, holds)
