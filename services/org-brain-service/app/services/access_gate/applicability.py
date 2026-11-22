"""Which approved rules apply to a case (issue 19).

The candidates come from SQL over `memory_record_current`, nothing else:
- `kind = rule`, `status = approved`, any enforcement (mandatory, prohibition
  and default: defaults are needed to resolve against constraints);
- the rule's schema belongs to the case's domain pack;
- `scope = ANY(readable ∩ path_up(requested scope))`: the caller passes OPA's
  readable scopes, which are already the requested scope and its ancestors
  (nearest first), never a sibling;
- `entities` empty, or sharing at least one key with the case's entities;
- valid at `as_of`, the business date of the event (Q8), never the clock.
Then each candidate's CEL condition is evaluated against the pack's context
for the case. A condition that cannot be evaluated is **reported, never
dropped**: the caller (the context pack, issue 20) must hold rather than
answer without a constraint it could not check.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.conditions import ConditionError
from app.conditions.cel import CelExprEvaluator
from app.domain_packs.base import DomainPack, RuleBody
from app.models import MemoryRecord
from app.services import ledger
from app.utils.business_dates import business_start

_cel = CelExprEvaluator()

_CANDIDATES = text(
    "SELECT record_id::text FROM memory_record_current "
    "WHERE kind = 'rule' AND status = 'approved' AND enforcement IS NOT NULL "
    "AND schema_version LIKE :schema "
    "AND scope = ANY(CAST(:scopes AS uuid[])) "
    "AND (cardinality(entities) = 0 OR entities && CAST(:entities AS text[])) "
    "AND valid_from <= :at AND (valid_to IS NULL OR valid_to > :at) "
    "ORDER BY record_id"
)


@dataclass(frozen=True)
class CaseFacts:
    """What the gate knows about one case: canonical entities and the CEL context."""

    entities: tuple[str, ...]
    context: Mapping[str, Any]


def case_facts(pack: DomainPack, case: Mapping[str, Any]) -> CaseFacts:
    matches = pack.resolve_entities(case)
    entities = tuple(sorted({m.key for m in matches if m.key}))
    return CaseFacts(entities, pack.condition_context(case, matches))


@dataclass(frozen=True)
class ApplicableRule:
    row: MemoryRecord
    body: RuleBody
    depth: int  # 0 = the requested scope itself, 1 = its parent, …

    @property
    def record_id(self) -> str:
        return self.row.record_id

    @property
    def enforcement(self) -> str:
        return str(self.row.enforcement)

    @property
    def authority(self) -> int:
        return self.row.authority


@dataclass(frozen=True)
class Unevaluable:
    record_id: str
    enforcement: str
    code: str
    detail: str


@dataclass(frozen=True)
class Applicability:
    rules: list[ApplicableRule]
    unevaluable: list[Unevaluable]


def applicable_rules(
    session: Session,
    *,
    pack: DomainPack,
    facts: CaseFacts,
    readable_scopes: Sequence[str],
    as_of: dt.date,
) -> Applicability:
    if not readable_scopes:
        return Applicability([], [])
    ids = session.execute(
        _CANDIDATES,
        {
            "schema": f"{pack.name}.rule/%",
            "scopes": list(readable_scopes),
            "entities": list(facts.entities),
            "at": business_start(as_of),
        },
    ).scalars()
    depth = {scope: i for i, scope in enumerate(readable_scopes)}
    rules: list[ApplicableRule] = []
    unevaluable: list[Unevaluable] = []
    for record_id in ids:
        row = ledger.latest(session, record_id)
        try:
            body = pack.rule_body_schema.model_validate(ledger.open_body(session, row))
            condition = _cel.compile(body.condition, variables=pack.condition_variables)
            applies = _cel.evaluate(condition, facts.context)
        except ValidationError as exc:
            unevaluable.append(
                Unevaluable(record_id, str(row.enforcement), "invalid_body", str(exc.errors()[0]))
            )
            continue
        except ConditionError as exc:
            unevaluable.append(Unevaluable(record_id, str(row.enforcement), exc.code, exc.message))
            continue
        if applies:
            rules.append(ApplicableRule(row, body, depth[row.scope]))
    return Applicability(rules, unevaluable)
