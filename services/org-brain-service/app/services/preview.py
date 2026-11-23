"""What changes when an owner approves a proposal (issue 52).

The origin case's pack, now and as if the proposal were approved with the
final enforcement and authority. The same applicability and resolution code
that builds real packs: the proposal is added as one more applicable rule
(when its entities and condition hold for the case), nothing is written.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.conditions import ConditionError
from app.conditions.cel import CelExprEvaluator
from app.domain_packs.base import DomainPack
from app.models import MemoryRecord
from app.services import ledger
from app.services.access_gate.applicability import ApplicableRule, applicable_rules, case_facts
from app.services.access_gate.resolve import Resolved, resolve

_cel = CelExprEvaluator()


@dataclass(frozen=True)
class PreviewRule:
    record_id: str
    text: str | None
    enforcement: str | None
    role: str
    overridden_by: str | None
    proposed: bool


@dataclass(frozen=True)
class Preview:
    applies: bool
    before: list[PreviewRule]
    after: list[PreviewRule]


def _as_approved(row: MemoryRecord, enforcement: str | None, authority: int) -> MemoryRecord:
    columns = MemoryRecord.__table__.columns.keys()
    copy = MemoryRecord(**{c: getattr(row, c) for c in columns})
    copy.enforcement = enforcement or row.enforcement
    copy.authority = authority
    copy.status = "approved"
    return copy


_ORDER = {"constraint": 0, "applied": 1, "overridden": 2}


def _listed(resolved: Sequence[Resolved], proposed_id: str) -> list[PreviewRule]:
    out = [
        PreviewRule(
            record_id=r.rule.record_id,
            text=getattr(r.rule.body, "text_ja", None),
            enforcement=r.rule.row.enforcement,
            role=r.role,
            overridden_by=r.overridden_by,
            proposed=r.rule.record_id == proposed_id,
        )
        for r in resolved
    ]
    return sorted(out, key=lambda p: (_ORDER[p.role], p.record_id))


def preview(
    session: Session,
    *,
    pack: DomainPack,
    case: Mapping[str, Any],
    readable_scopes: Sequence[str],
    as_of: dt.date,
    proposal: MemoryRecord,
    enforcement: str | None,
    authority: int,
) -> Preview:
    facts = case_facts(pack, case)
    now = applicable_rules(
        session, pack=pack, facts=facts, readable_scopes=readable_scopes, as_of=as_of
    )
    body = pack.rule_body_schema.model_validate(ledger.open_body(session, proposal))
    entities_ok = not proposal.entities or bool(set(proposal.entities) & set(facts.entities))
    try:
        holds = _cel.evaluate(
            _cel.compile(body.condition, variables=pack.condition_variables), facts.context
        )
    except ConditionError:
        holds = False
    applies = entities_ok and bool(holds)
    rules = list(now.rules)
    if applies:
        scopes = list(readable_scopes)
        depth = scopes.index(proposal.scope) if proposal.scope in scopes else 0
        rules.append(ApplicableRule(_as_approved(proposal, enforcement, authority), body, depth))
    return Preview(
        applies=applies,
        before=_listed(resolve(now.rules), proposal.record_id),
        after=_listed(resolve(rules), proposal.record_id),
    )
