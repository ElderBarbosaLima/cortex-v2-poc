"""The domain pack interface (D18): everything workflow-specific, nothing else.

The core (ledger, admission gate, access gate, verify, writeback, evidence)
talks to a workflow only through this protocol, so it never needs to know
which workflow is running. A pack brings:
- its entity types and the resolver that maps a case to canonical entity keys
  (`customer:CUST-0042`), with confidence and evidence;
- what rule conditions may see: the CEL variables, the fields of each, and
  which fields name an entity (so the core can check a condition against the
  rule's `entities[]`, Q6);
- the rule targets (conflict keys) it knows, and the schemas of a rule body and
  of a reviewer correction;
- how a correction becomes a proposed rule, the text a case is searched by,
  and an optional check of an output against the rules in its pack (verify);
- its synthetic data and golden set (synthetic.py next to it).
If core code ever needs to branch on the workflow's name, that is a design bug: stop and report.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

CONFIDENCE_BANDS = ("high", "low", "ambiguous", "none")
_ENTITY_KEY = re.compile(r"^[a-z][a-z_]*:[A-Za-z0-9][A-Za-z0-9._-]*$")


class RuleBody(BaseModel):
    """The minimum every pack's rule body carries; packs extend it."""

    text_ja: str
    targets: list[str]
    condition: str


@dataclass(frozen=True)
class EntityMatch:
    key: str | None  # canonical "<type>:<id>", None when nothing resolved confidently
    confidence: str  # one of CONFIDENCE_BANDS
    evidence: Mapping[str, Any] = field(default_factory=dict[str, Any])
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleDraft:
    """What a pack proposes from a correction. The core admits it like any other
    proposal: authority stays at or below the model ceiling until a person approves."""

    body: Mapping[str, Any]
    entities: tuple[str, ...]
    enforcement: str = "default"
    authority: int = 10


class DomainPack(Protocol):
    # Read-only members: a pack declares them as plain class attributes.
    @property
    def name(self) -> str: ...

    @property
    def entity_types(self) -> frozenset[str]: ...

    @property
    def condition_variables(self) -> tuple[str, ...]: ...

    @property
    def condition_fields(self) -> Mapping[str, frozenset[str]]:
        """variable -> the fields a condition may read."""
        ...

    @property
    def entity_fields(self) -> Mapping[str, str]:
        """ "invoice.customer_id" -> "customer": fields that name an entity."""
        ...

    @property
    def targets(self) -> frozenset[str]: ...

    @property
    def rule_body_schema(self) -> type[RuleBody]: ...

    @property
    def correction_schema(self) -> type[BaseModel]: ...

    def resolve_entities(self, case: Mapping[str, Any]) -> list[EntityMatch]: ...

    def condition_context(
        self, case: Mapping[str, Any], entities: Sequence[EntityMatch]
    ) -> dict[str, Any]: ...

    def correction_to_rule(
        self, correction: BaseModel, *, case: Mapping[str, Any]
    ) -> RuleDraft: ...

    def search_text(self, case: Mapping[str, Any]) -> str: ...

    def check_output(
        self, output: Mapping[str, Any], rules: Sequence[RuleBody], case: Mapping[str, Any]
    ) -> list[str]:
        """Problems with `output` given the rules in its pack and the case it was
        served for (empty = none). The case lets a pack judge what rules alone
        cannot (e.g. a payer resolved without confidence must not be
        auto-matched). Optional for verify; a pack that cannot check returns []."""
        ...


def is_entity_key(key: str, entity_types: frozenset[str]) -> bool:
    return bool(_ENTITY_KEY.match(key)) and key.split(":", 1)[0] in entity_types


_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def referenced_fields(condition: str, variables: Sequence[str]) -> set[str]:
    """`var.field` references in a condition, ignoring string literals."""
    code = _STRING.sub('""', condition)
    names = "|".join(re.escape(v) for v in variables)
    return set(re.findall(rf"\b(?:{names})\.[A-Za-z_][A-Za-z0-9_]*", code))


def entity_literals(condition: str, entity_fields: Mapping[str, str]) -> set[str]:
    """Entity keys a condition names by literal, e.g. `invoice.customer_id ==
    "CUST-0042"` → `customer:CUST-0042` (either operand order)."""
    found: set[str] = set()
    for path, entity_type in entity_fields.items():
        p = re.escape(path)
        for match in re.finditer(rf'\b{p}\s*==\s*"([^"]+)"|"([^"]+)"\s*==\s*\b{p}\b', condition):
            found.add(f"{entity_type}:{match.group(1) or match.group(2)}")
    return found


class UnknownPackError(LookupError):
    pass


_PACKS: dict[str, DomainPack] = {}


def register_pack(pack: DomainPack) -> DomainPack:
    if pack.name in _PACKS and _PACKS[pack.name] is not pack:
        raise ValueError(f"domain pack {pack.name!r} is already registered")
    _PACKS[pack.name] = pack
    return pack


def get_pack(name: str) -> DomainPack:
    try:
        return _PACKS[name]
    except KeyError:
        raise UnknownPackError(f"no domain pack {name!r}") from None


def registered_packs() -> list[str]:
    return sorted(_PACKS)
