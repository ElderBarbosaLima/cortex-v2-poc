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
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
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
from app.services.body_codec import BodyUnreadableError, body_hash
from app.utils.ids import new_record_id

SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
NEEDS_REVIEW = frozenset({Kind.RULE, Kind.FACT})
_cel = CelExprEvaluator()


@dataclass(frozen=True)
class Problem:
    code: str
    detail: str
    record_id: str | None = None  # the record the problem points at, when there is one


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
    supersedes: str | None = None  # an approved record this one replaces once approved


def _rule_problems(pack: DomainPack, proposal: Proposal) -> list[Problem]:
    problems: list[Problem] = []
    if proposal.enforcement is None:
        problems.append(
            Problem("missing_enforcement", "a rule needs mandatory, prohibition or default")
        )
    try:
        body = pack.rule_body_schema.model_validate(proposal.body)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        msg = str(first.get("msg", exc))
        return [*problems, Problem("invalid_body", f"{where}: {msg}" if where else msg)]
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


_ANCESTOR_CONSTRAINTS = text(
    "SELECT record_id::text FROM memory_record_current "
    "WHERE kind = 'rule' AND status = 'approved' "
    "AND enforcement IN ('mandatory', 'prohibition') "
    "AND schema_version = :schema AND scope = ANY(CAST(:ancestors AS uuid[])) "
    # A constraint counts when it covers the proposal's cases: a global one, or one
    # about an entity the proposal names. A rule for all customers does not loosen
    # one customer's constraint (issue 50: that was a false positive).
    "AND (cardinality(entities) = 0 OR entities && CAST(:entities AS text[])) "
    "AND (valid_to IS NULL OR valid_to > now()) "
    "ORDER BY record_id"
)


def inherited_conflicts(
    session: Session, proposal: Proposal, ancestors: Sequence[str]
) -> list[Problem]:
    """Scopes only tighten (issue 31): a `default` rule at a lower scope that
    shares a target with an approved constraint (mandatory or prohibition) at
    an ancestor scope, for compatible entities, would loosen it. Read-time
    resolution would still make the constraint win; this says so at intake.
    A constraint proposed below an ancestor constraint tightens: no problem."""
    if proposal.kind is not Kind.RULE or proposal.enforcement != "default" or not ancestors:
        return []
    targets = set(proposal.body.get("targets") or [])
    ids = session.execute(
        _ANCESTOR_CONSTRAINTS,
        {
            "schema": proposal.schema_version,
            "ancestors": list(ancestors),
            "entities": list(proposal.entities),
        },
    ).scalars()
    problems: list[Problem] = []
    for record_id in ids:
        row = ledger.latest(session, record_id)
        try:
            shared = targets & set(ledger.open_body(session, row).get("targets") or [])
        except BodyUnreadableError:
            continue  # an erased constraint constrains nothing
        if shared:
            problems.append(
                Problem(
                    "loosens_inherited_constraint",
                    f"{row.enforcement} {record_id} at an ancestor scope governs "
                    f"{sorted(shared)}; a lower scope may only tighten it",
                    record_id=str(record_id),
                )
            )
    return problems


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
        supersedes=proposal.supersedes,
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


def supersede_problems(session: Session, proposal: Proposal) -> list[Problem]:
    """What a proposal may replace (issue 33): an approved (or stale) record of
    the same domain and kind, visible in this tenant."""
    if proposal.supersedes is None:
        return []
    try:
        old = ledger.latest(session, proposal.supersedes)
    except ledger.RecordNotFoundError:
        return [Problem("unknown_superseded", f"no record {proposal.supersedes}")]
    if old.status not in ("approved", "stale"):
        return [Problem("not_supersedable", f"{proposal.supersedes} is {old.status}")]
    if old.kind != proposal.kind.value or old.schema_version != proposal.schema_version:
        return [Problem("supersede_mismatch", "a record replaces one of its own domain and kind")]
    return []


@dataclass(frozen=True)
class SameContent:
    record_id: str
    same_validity: bool


def approved_same_content(
    session: Session,
    schema_version: str,
    scope: str,
    entities: Sequence[str],
    body: dict[str, Any],
    valid_from: dt.datetime,
    valid_to: dt.datetime | None,
) -> SameContent | None:
    """An approved, current record with this exact content at this scope (issue
    37, scenario 1). Same validity too: a re-import changes nothing. Other
    validity: it is a change to review, never a silent renewal."""
    row = session.execute(
        text(
            "SELECT record_id::text, valid_from, valid_to FROM memory_record_current "
            "WHERE status = 'approved' AND schema_version = :schema "
            "AND scope = CAST(:scope AS uuid) AND body_hash = :hash "
            "AND entities = CAST(:entities AS text[]) ORDER BY record_id LIMIT 1"
        ),
        {
            "schema": schema_version,
            "scope": scope,
            "hash": body_hash(body),
            "entities": list(entities),
        },
    ).first()
    if row is None:
        return None
    return SameContent(str(row[0]), row[1] == valid_from and row[2] == valid_to)


def pending_same_content(
    session: Session,
    schema_version: str,
    scope: str,
    entities: Sequence[str],
    body: dict[str, Any],
    valid_from: dt.datetime,
    valid_to: dt.datetime | None,
    *,
    asserted_by: dict[str, Any],
    supersedes: str | None,
) -> tuple[str, str] | None:
    """The same proposer's proposal with this exact content, validity and
    `supersedes`, still waiting for a decision: (record_id, review case). A
    repeat returns it instead of queueing the same rule twice (issue 39). A
    different proposer is new evidence and still makes its own proposal."""
    row = session.execute(
        text(
            "SELECT r.record_id::text, i.case_id::text FROM memory_record_current r "
            "JOIN review_case_item i ON i.record_id = r.record_id "
            "JOIN review_case c ON c.case_id = i.case_id AND c.status = 'open' "
            "WHERE r.status = 'proposed' AND r.schema_version = :schema "
            "AND r.scope = CAST(:scope AS uuid) AND r.body_hash = :hash "
            "AND r.entities = CAST(:entities AS text[]) AND r.valid_from = :vf "
            "AND r.valid_to IS NOT DISTINCT FROM :vt "
            "AND r.supersedes IS NOT DISTINCT FROM CAST(:sup AS uuid) "
            "AND r.asserted_by = CAST(:by AS jsonb) ORDER BY r.record_id LIMIT 1"
        ),
        {
            "schema": schema_version,
            "scope": scope,
            "hash": body_hash(body),
            "entities": list(entities),
            "vf": valid_from,
            "vt": valid_to,
            "sup": supersedes,
            "by": json.dumps(asserted_by, default=str),
        },
    ).first()
    return (str(row[0]), str(row[1])) if row else None


def problems_payload(problems: Sequence[Problem]) -> list[dict[str, str]]:
    return [
        {"code": p.code, "detail": p.detail, **({"record_id": p.record_id} if p.record_id else {})}
        for p in problems
    ]
