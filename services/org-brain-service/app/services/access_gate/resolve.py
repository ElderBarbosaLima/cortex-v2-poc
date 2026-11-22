"""Resolution (issue 19, Rev. B rule 4): per target, who wins.

- A constraint (prohibition or mandatory) never loses: it is always kept.
- A default that shares a target with a constraint is overridden by it
  (attributed to a prohibition before a mandatory rule).
- Among defaults on a shared target, higher authority wins, then the nearer
  scope; the loser is labelled `overridden_by` the best winner.
Ties (same authority, same scope) override nothing: both stay applied and a
person sees both. This mirrors the golden-set oracle in
`app/synthetic/common.py::expected_rules`, written independently.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.services.access_gate.applicability import ApplicableRule

Role = Literal["constraint", "applied", "overridden"]
_CONSTRAINT_RANK = {"prohibition": 0, "mandatory": 1}


@dataclass(frozen=True)
class Resolved:
    rule: ApplicableRule
    role: Role
    overridden_by: str | None = None


def _outranks(winner: ApplicableRule, loser: ApplicableRule) -> bool:
    return (winner.authority, -winner.depth) > (loser.authority, -loser.depth)


def resolve(rules: Sequence[ApplicableRule]) -> list[Resolved]:
    out: list[Resolved] = []
    for rule in rules:
        if rule.enforcement in _CONSTRAINT_RANK:
            out.append(Resolved(rule, "constraint"))
            continue
        targets = set(rule.body.targets)
        rivals = [r for r in rules if r is not rule and targets & set(r.body.targets)]
        constraints = sorted(
            (r for r in rivals if r.enforcement in _CONSTRAINT_RANK),
            key=lambda r: (_CONSTRAINT_RANK[r.enforcement], r.record_id),
        )
        defaults = sorted(
            (r for r in rivals if r.enforcement == "default" and _outranks(r, rule)),
            key=lambda r: (-r.authority, r.depth, r.record_id),
        )
        winner = (constraints or defaults or [None])[0]
        if winner is None:
            out.append(Resolved(rule, "applied"))
        else:
            out.append(Resolved(rule, "overridden", winner.record_id))
    return out
