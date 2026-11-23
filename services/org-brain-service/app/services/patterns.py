"""Spot repeated patterns in a history of resolved payments (issue 46).

IBM: "Cortex spots a pattern, e.g. same fix 6 times", and the slide 17 figures
("repeated patterns found in [__] months of CAT matches; rules Arbitr would
suggest"). This is the batch side of the learning loop: it reads a history of
manual resolutions (CAT's export, once IBM shares it; synthetic until then),
groups them by conflict key (the pack's targets × entities: here, exception
type × customer), and for every group that reaches the domain's threshold
proposes ONE rule through the admission gate, for its owner to approve.

- The rule is the pack's own draft from the group's most typical case: the
  one whose `gap` (from the pack's case summary) is the most frequent.
- It is asserted by `policy` (pattern-mining), capped at the model ceiling,
  never approved here; its source says how many cases and which.
- Idempotent: a group whose rule is already approved, or already waiting, is
  reported, not proposed again.
Generic over packs: no workflow is named here.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.domain_packs.base import DomainPack, NoRuleToLearnError
from app.models import Kind
from app.services import admission, ledger
from app.services.writeback import RuleSuggestionPolicy
from app.utils.business_dates import NO_LOWER_BOUND, business_start

MINER = {"type": "policy", "id": "pattern-mining"}


@dataclass
class Found:
    conflict_key: str
    entities: tuple[str, ...]
    targets: tuple[str, ...]
    cases: int
    outcome: str  # proposed | below_threshold | already_approved | already_waiting | rejected
    record_id: str | None = None
    review_case_id: str | None = None
    problems: list[str] = field(default_factory=list[str])

    def as_json(self) -> dict[str, Any]:
        return {
            "entities": list(self.entities),
            "targets": list(self.targets),
            "cases": self.cases,
            "outcome": self.outcome,
            "record_id": self.record_id,
            "review_case_id": self.review_case_id,
            "problems": self.problems,
        }


@dataclass(frozen=True)
class _Row:
    case_ref: str
    case: Mapping[str, Any]
    decided_at: dt.datetime
    gap: Any
    proposal: admission.Proposal


def mine(
    session: Session,
    *,
    tenant_id: str,
    pack: DomainPack,
    scope: str,
    history: Iterable[Mapping[str, Any]],
    policy: RuleSuggestionPolicy,
    now: dt.datetime,
) -> list[Found]:
    """`history` rows: {"case": <the pack's case>, "outcome": <the pack's
    correction>, "decided_at": ISO 8601}. Only rows inside the window count."""
    since = now - dt.timedelta(days=policy.window_days)
    groups: dict[str, list[_Row]] = defaultdict(list)
    for item in history:
        decided_at = dt.datetime.fromisoformat(str(item["decided_at"]))
        if decided_at < since or decided_at > now:
            continue
        outcome = pack.correction_schema.model_validate(
            {**item["outcome"], "result": "corrected"}  # a manual resolution is a correction
        )
        try:
            draft = pack.correction_to_rule(outcome, case=item["case"])
        except NoRuleToLearnError:
            continue
        proposal = admission.Proposal(
            domain=pack.name,
            kind=Kind.RULE,
            schema_version=f"{pack.name}.rule/1",
            body=dict(draft.body),
            scope=scope,
            entities=draft.entities,
            enforcement=draft.enforcement,
            authority=min(draft.authority, ledger.MODEL_AUTHORITY_CEILING),
            sensitivity={"class": "internal", "personal_data": False},
            valid_from=business_start(NO_LOWER_BOUND),
        )
        summary = pack.case_summary(item["case"], outcome)
        case_ref = str(getattr(outcome, "case_id", "") or summary.get("case_id", ""))
        groups[admission.conflict_key(proposal)].append(
            _Row(case_ref, item["case"], decided_at, summary.get("gap"), proposal)
        )

    found: list[Found] = []
    for key, rows in sorted(groups.items()):
        cases = sorted({r.case_ref for r in rows})
        sample = rows[0].proposal
        report = Found(
            key, sample.entities, tuple(sample.body.get("targets") or ()), len(cases), ""
        )
        if len(cases) < policy.min_distinct_cases:
            report.outcome = "below_threshold"
            found.append(report)
            continue
        typical = Counter(r.gap for r in rows).most_common(1)[0][0]
        chosen = max((r for r in rows if r.gap == typical), key=lambda r: r.decided_at)
        proposal = _with_source(chosen.proposal, cases, policy)
        same = admission.approved_same_content(
            session,
            proposal.schema_version,
            proposal.scope,
            proposal.entities,
            proposal.body,
            proposal.valid_from,
            proposal.valid_to,
        )
        if same is not None:
            report.outcome, report.record_id = "already_approved", same.record_id
            found.append(report)
            continue
        waiting = admission.pending_same_content(
            session,
            proposal.schema_version,
            proposal.scope,
            proposal.entities,
            proposal.body,
            proposal.valid_from,
            proposal.valid_to,
            asserted_by=MINER,
            supersedes=None,
        )
        if waiting is not None:
            report.outcome = "already_waiting"
            report.record_id, report.review_case_id = waiting
            found.append(report)
            continue
        _, problems = admission.validate_proposal(proposal)
        if problems:
            report.outcome, report.problems = (
                "rejected",
                [f"{p.code}: {p.detail}" for p in problems],
            )
            found.append(report)
            continue
        admitted = admission.admit(
            session, tenant_id=tenant_id, proposal=proposal, asserted_by=MINER
        )
        report.outcome = "proposed"
        report.record_id, report.review_case_id = admitted.record.record_id, admitted.review_case_id
        found.append(report)
    return found


def _with_source(
    proposal: admission.Proposal, cases: list[str], policy: RuleSuggestionPolicy
) -> admission.Proposal:
    from dataclasses import replace

    return replace(
        proposal,
        source={
            "suggested_by": "pattern-mining",
            "case_refs": cases[:50],
            "pattern": {
                "cases": len(cases),
                "window_days": policy.window_days,
                "threshold": policy.min_distinct_cases,
            },
        },
    )
