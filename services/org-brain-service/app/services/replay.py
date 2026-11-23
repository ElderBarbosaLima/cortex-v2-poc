"""Replay and shadow scoring (issue 49, slide 26).

Replays resolved payments through Cortex as they were on the day (each pack is
asked as of the payment's date) and scores what Cortex gave against what the
specialist actually did. Pure: the caller fetches the packs; this only counts.

Measures (slide 26, "measured in replay and shadow"):
- `rule_evidence`: of the payments a rule settled, how many cite that rule
  with its version and approver. Target: every one (IBM's audit trail).
- `covered`: payments the rules alone would have settled, so no specialist
  was needed. Either there was nothing to resolve (gap 0, one candidate) or an
  applied rule handles the exception the specialist found. This is the PoC's
  proxy for "payments cleared without a specialist" (74% today → 85%).
- `agreement`: of the payments a rule covered, how often its engine fields
  agree with what the specialist did. Target 90% before clearing alone.
Suggestion acceptance (70%) comes from the review queue, not from replay.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

TARGETS = {"covered": 0.85, "rule_evidence": 1.0, "agreement": 0.90, "suggestions_accepted": 0.70}


@dataclass(frozen=True)
class Replayed:
    case_ref: str
    gap: int
    candidates: int
    specialist_exception: str | None
    specialist_engine: Mapping[str, Any] | None
    rules: Sequence[Mapping[str, Any]]  # the pack's rule records: role, approved_by, body


@dataclass
class Score:
    payments: int = 0
    with_evidence: int = 0
    covered: int = 0
    covered_by_rule: int = 0
    agreed: int = 0
    misses: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def as_json(self) -> dict[str, Any]:
        def ratio(n: int, d: int) -> float | None:
            return round(n / d, 3) if d else None

        return {
            "payments": self.payments,
            "rule_evidence": ratio(self.with_evidence, self.covered_by_rule),
            "covered": ratio(self.covered, self.payments),
            "agreement": ratio(self.agreed, self.covered_by_rule),
            "misses": self.misses[:50],
        }


def _applied(rules: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in rules if r.get("role") in ("constraint", "applied") and r.get("body")]


def _agrees(engine: Mapping[str, Any], specialist: Mapping[str, Any] | None) -> bool:
    if not specialist:
        return True  # the specialist named the exception only: the type is what we can compare
    return all(engine.get(k) == v for k, v in specialist.items() if k in engine)


def score(replayed: Sequence[Replayed]) -> Score:
    s = Score()
    for r in replayed:
        s.payments += 1
        applied = _applied(r.rules)
        if r.specialist_exception is None and r.gap == 0 and r.candidates <= 1:
            s.covered += 1  # a plain match: the rule engine needs nothing from Cortex
            continue
        handling = [x for x in applied if x["body"].get("exception") == r.specialist_exception]
        if handling:
            s.covered += 1
            s.covered_by_rule += 1
            if all(x.get("approved_by") for x in handling):
                s.with_evidence += 1
            if any(_agrees(x["body"].get("engine") or {}, r.specialist_engine) for x in handling):
                s.agreed += 1
            else:
                s.misses.append(
                    {
                        "case": r.case_ref,
                        "why": "a rule covered it, but its engine fields "
                        "differ from what the specialist did",
                    }
                )
        else:
            s.misses.append(
                {"case": r.case_ref, "why": f"no applied rule for {r.specialist_exception}"}
            )
    return s
