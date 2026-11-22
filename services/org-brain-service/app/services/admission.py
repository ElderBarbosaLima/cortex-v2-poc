"""The admission gate (Rev. B: tag & classify → typed record → resolve entities
→ admission policy → review queue). Every write into Cortex passes here.

Two halves:
- `validate_proposal` is pure: no database, no OPA. It returns every problem at
  once, as `{code, detail}`, so a caller can fix them all in one go.
- `admit` asks OPA (who may propose here, and the purpose's sensitivity
  ceiling), then appends the record as `proposed` and opens, or joins, the one
  open review case for the same conflict (tenant + targets + entities).
Nothing is ever admitted as approved: machines propose, people approve (rule 6).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.conditions import ConditionCompileError
from app.conditions.cel import CelExprEvaluator
from app.domain_packs.base import (
    DomainPack,
    UnknownPackError,
    entity_literals,
    get_pack,
    is_entity_key,
    referenced_fields,
)
from app.models import Kind, ReviewCase, ReviewCaseItem
from app.services import ledger
from app.utils.ids import new_record_id

SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
NEEDS_REVIEW = frozenset({Kind.RULE, Kind.FACT})
_cel = CelExprEvaluator()


@dataclass(frozen=True)
class Problem:
    code: str
    detail: str


@dataclass(frozen=True)
class Proposal:
    domain: str
    kind: Kind
    schema_version: str
    body: dict[str, Any]
    scope: str
    entities: tuple[str, ...]
    enforcement: str | None
    authority: int
    sensitivity: dict[str, Any]
    valid_from: dt.datetime
    valid_to: dt.datetime | None = None
    source: dict[str, Any] = field(default_factory=dict[str, Any])
    derived_from: tuple[str, ...] = ()


def _rule_problems(pack: DomainPack, proposal: Proposal) -> list[Problem]:
    problems: list[Problem] = []
    if proposal.enforcement is None:
        problems.append(
            Problem("missing_enforcement", "a rule needs mandatory, prohibition or default")
        )
    try:
        body = pack.rule_body_schema.model_validate(proposal.body)
    except ValidationError as exc:
        return [*problems, Problem("invalid_body", str(exc.errors()[0].get("msg", exc)))]
    unknown = sorted(set(body.targets) - pack.targets)
    if not body.targets or unknown:
        problems.append(
            Problem(
                "unknown_target", f"targets {unknown or body.targets} are not known to {pack.name}"
            )
        )
    try:
        _cel.compile(body.condition, variables=pack.condition_variables)
    except ConditionCompileError as exc:
        problems.append(Problem("invalid_condition", exc.message))
        return problems
    allowed = {f"{v}.{f}" for v, fields in pack.condition_fields.items() for f in fields}
    unknown_fields = sorted(referenced_fields(body.condition, pack.condition_variables) - allowed)
    if unknown_fields:
        problems.append(Problem("unknown_field", f"the condition reads {unknown_fields}"))
    missing = sorted(entity_literals(body.condition, pack.entity_fields) - set(proposal.entities))
    if missing:
        problems.append(
            Problem("entity_mismatch", f"the condition names {missing} but entities[] does not")
        )
    return problems


def validate_proposal(proposal: Proposal) -> tuple[DomainPack | None, list[Problem]]:
    try:
        pack = get_pack(proposal.domain)
    except UnknownPackError:
        return None, [Problem("unknown_domain", f"no domain pack {proposal.domain!r}")]
    problems: list[Problem] = []
    expected = f"{pack.name}.{proposal.kind.value}/1"
    if proposal.schema_version != expected:
        problems.append(Problem("unknown_schema_version", f"expected {expected}"))
    bad = [e for e in proposal.entities if not is_entity_key(e, pack.entity_types)]
    if bad:
        problems.append(
            Problem(
                "non_canonical_entity", f"{bad} are not canonical {sorted(pack.entity_types)} keys"
            )
        )
    if proposal.sensitivity.get("class") not in SENSITIVITY_RANK:
        problems.append(
            Problem("invalid_sensitivity", f"class must be one of {list(SENSITIVITY_RANK)}")
        )
    if proposal.kind is Kind.RULE:
        problems += _rule_problems(pack, proposal)
    return pack, problems


def conflict_key(proposal: Proposal) -> str:
    targets = sorted(proposal.body.get("targets", []) or [proposal.kind.value])
    raw = "|".join([",".join(targets), ",".join(sorted(proposal.entities))])
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Admitted:
    record: Any
    review_case_id: str | None
    grouped: bool


def _join_or_open_case(
    session: Session, tenant_id: str, proposal: Proposal, record_id: str
) -> tuple[str, bool]:
    key = conflict_key(proposal)
    case = session.scalars(
        select(ReviewCase).where(ReviewCase.conflict_key == key, ReviewCase.status == "open")
    ).first()
    grouped = case is not None
    if case is None:
        case = ReviewCase(
            case_id=new_record_id(),
            tenant_id=tenant_id,
            conflict_key=key,
            scope=proposal.scope,
        )
        session.add(case)
        session.flush()
    session.add(ReviewCaseItem(tenant_id=tenant_id, case_id=case.case_id, record_id=record_id))
    session.flush()
    return case.case_id, grouped


def admit(
    session: Session, *, tenant_id: str, proposal: Proposal, asserted_by: dict[str, Any]
) -> Admitted:
    """Append a validated proposal and route it to review. The caller has
    already validated it and obtained OPA's permission."""
    row = ledger.append_new(
        session,
        tenant_id=tenant_id,
        draft=ledger.RecordDraft(
            kind=proposal.kind,
            schema_version=proposal.schema_version,
            body=proposal.body,
            scope=proposal.scope,
            asserted_by=asserted_by,
            authority=proposal.authority,
            source=proposal.source,
            valid_from=proposal.valid_from,
            valid_to=proposal.valid_to,
            enforcement=proposal.enforcement,
            entities=proposal.entities,
            assurance_level="unverified",
            derived_from=proposal.derived_from,
            sensitivity=proposal.sensitivity,
        ),
    )
    if proposal.kind not in NEEDS_REVIEW:
        return Admitted(row, None, False)
    case_id, grouped = _join_or_open_case(session, tenant_id, proposal, row.record_id)
    return Admitted(row, case_id, grouped)


def problems_payload(problems: Sequence[Problem]) -> list[dict[str, str]]:
    return [{"code": p.code, "detail": p.detail} for p in problems]
