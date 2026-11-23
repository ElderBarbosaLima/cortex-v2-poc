"""The review queue: a named owner decides on a case of proposals (doc 06, D17).

The caller asks OPA first (approve/reject: a person, holding memory.approve,
not a contributor, the named owner or backup of the scope); this module only
records what was decided. One decision does three things in one transaction:

- a `kind=decision` ledger record (the decision as memory: who, what, why),
  `derived_from` the case's proposals so erasing their lineage erases it too;
- the next version of each pending proposal:
  - approve: the chosen one becomes `approved`, with
    `approved_by {approver_type: user, user_id, decision_id}` and the final
    authority/enforcement; the other pending proposals in the case (the same
    conflict, e.g. duplicates from many jobs) become `superseded`;
  - reject: every pending one becomes `deprecated` with no `approved_by`
    (Q5: Rev. B's six statuses, no `rejected` status);
- an append-only `review_decision` row, and the case moves to `decided`.

`outcome` is derived from history, so nobody has to infer it (Q5).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Kind, MemoryRecord, ReviewCase, ReviewCaseItem, ReviewDecision, Status
from app.services import ledger
from app.services.admission import SENSITIVITY_RANK
from app.services.body_codec import BodyUnreadableError
from app.utils.ids import new_record_id

Outcome = Literal["pending", "approved", "rejected", "retired", "superseded"]
DECISION_SCHEMA = "cortex.decision/1"


class CaseNotFoundError(LookupError):
    pass


class CaseDecidedError(ValueError):
    pass


class InvalidDecisionError(ValueError):
    pass


def outcome(status: str, ever_approved: bool) -> Outcome:
    """pending | approved | rejected | retired | superseded, from the current
    status and whether any version was ever approved. Deprecated without ever
    being approved is a rejection; deprecated after approval is a retirement.
    Erased counts as retired (out of use; issue 24 owns erasure)."""
    match Status(status):
        case Status.PROPOSED:
            return "pending"
        case Status.APPROVED | Status.STALE:
            return "approved"
        case Status.SUPERSEDED:
            return "superseded"
        case Status.DEPRECATED:
            return "retired" if ever_approved else "rejected"
        case Status.ERASED:
            return "retired"


def ever_approved(session: Session, record_ids: Sequence[str]) -> set[str]:
    rows = session.execute(
        text(
            "SELECT DISTINCT record_id::text FROM memory_record "
            "WHERE record_id = ANY(CAST(:ids AS uuid[])) AND status = 'approved'"
        ),
        {"ids": list(record_ids)},
    ).scalars()
    return set(rows)


@dataclass(frozen=True)
class OpenCase:
    case: ReviewCase
    items: list[MemoryRecord]  # current version of each proposal, in arrival order

    @property
    def pending(self) -> list[MemoryRecord]:
        return [r for r in self.items if r.status == Status.PROPOSED.value]

    @property
    def contributors(self) -> list[str]:
        """People whose assertions or corrections produced the proposals: none of
        them may decide (separation of duties, D17)."""
        people: set[str] = set()
        for row in self.items:
            if row.asserted_by.get("type") == "user":
                people.add(str(row.asserted_by["id"]))
            people.update(str(c) for c in row.source.get("contributors", []))
        return sorted(people)

    @property
    def sensitivity_class(self) -> str:
        return max((r.sensitivity_class for r in self.items), key=SENSITIVITY_RANK.__getitem__)


def load_case(session: Session, case_id: str, *, for_update: bool = False) -> OpenCase:
    query = select(ReviewCase).where(ReviewCase.case_id == case_id)
    case = session.scalars(query.with_for_update() if for_update else query).first()
    if case is None:
        raise CaseNotFoundError(case_id)
    record_ids = session.scalars(
        select(ReviewCaseItem.record_id)
        .where(ReviewCaseItem.case_id == case_id)
        .order_by(ReviewCaseItem.id)
    ).all()
    return OpenCase(case, [ledger.latest(session, r) for r in record_ids])


ERASED_TITLE = "(erased)"


def body_or_none(session: Session, row: MemoryRecord) -> dict[str, Any] | None:
    """The record's body, or None once its key was erased (issue 24)."""
    try:
        return ledger.open_body(session, row)
    except BodyUnreadableError:
        return None


def title(session: Session, row: MemoryRecord) -> str:
    """Read from the sealed body, never stored in clear (migration 005). An
    erased body has no title: a case with one stays listable."""
    body = body_or_none(session, row)
    if body is None:
        return ERASED_TITLE
    return str(body.get("text_ja") or row.kind)[:200]


def decisions(session: Session, case_id: str) -> list[ReviewDecision]:
    return list(
        session.scalars(
            select(ReviewDecision)
            .where(ReviewDecision.case_id == case_id)
            .order_by(ReviewDecision.id)
        )
    )


@dataclass(frozen=True)
class Decided:
    decision: ReviewDecision
    versions: list[MemoryRecord]  # the new version of every proposal it touched


def _decision_record(
    session: Session,
    *,
    tenant_id: str,
    decision_id: str,
    oc: OpenCase,
    user_id: str,
    body: dict[str, Any],
) -> None:
    entities = sorted({e for r in oc.items for e in r.entities})
    ledger.append_new(
        session,
        tenant_id=tenant_id,
        record_id=decision_id,
        status=Status.APPROVED,
        approved_by={"approver_type": "user", "user_id": user_id, "decision_id": decision_id},
        draft=ledger.RecordDraft(
            kind=Kind.DECISION,
            schema_version=DECISION_SCHEMA,
            body={"case_id": oc.case.case_id, **body},
            scope=oc.case.scope,
            asserted_by={"type": "user", "id": user_id},
            authority=0,  # episodic memory, not a rule: it outranks nothing
            source={"review_case": oc.case.case_id},
            valid_from=dt.datetime.now(dt.UTC),
            entities=tuple(entities),
            assurance_level="reviewed",
            derived_from=tuple(r.record_id for r in oc.items),
            sensitivity={"class": oc.sensitivity_class},
        ),
    )


def _close(
    session: Session, oc: OpenCase, row: ReviewDecision, versions: list[MemoryRecord]
) -> Decided:
    session.add(row)
    oc.case.status = "decided"
    session.flush()
    return Decided(row, versions)


def _require_open(oc: OpenCase) -> None:
    if oc.case.status != "open" or not oc.pending:
        raise CaseDecidedError(f"case {oc.case.case_id} is already decided")


def approve(
    session: Session,
    *,
    tenant_id: str,
    oc: OpenCase,
    user_id: str,
    record_id: str | None,
    authority: int,
    enforcement: str | None,
    reason: str | None,
    policy_revision: str | None,
    valid_from: dt.datetime | None = None,
    valid_to: dt.datetime | None = None,
) -> Decided:
    _require_open(oc)
    pending = {r.record_id: r for r in oc.pending}
    chosen = pending.get(record_id) if record_id else oc.pending[0]
    if chosen is None:
        raise InvalidDecisionError(f"{record_id} is not a pending proposal in this case")
    if chosen.kind != Kind.RULE.value and enforcement is not None:
        raise InvalidDecisionError("only a rule has an enforcement")
    decision_id = new_record_id()
    final_enforcement = enforcement or chosen.enforcement
    _decision_record(
        session,
        tenant_id=tenant_id,
        decision_id=decision_id,
        oc=oc,
        user_id=user_id,
        body={
            "decision": "approve",
            "approved_record_id": chosen.record_id,
            "authority": authority,
            "enforcement": final_enforcement,
            "reason": reason,
        },
    )
    versions = [
        ledger.transition(
            session,
            record_id=chosen.record_id,
            to=Status.APPROVED,
            approved_by={"approver_type": "user", "user_id": user_id, "decision_id": decision_id},
            authority=authority,
            enforcement=enforcement,
            valid_from=valid_from,
            valid_to=valid_to,
        )
    ]
    if chosen.supersedes:
        # The approved record replaces the old one (issue 33): packs that relied
        # on it are held by verify, the next pack carries the new record.
        old = ledger.latest(session, chosen.supersedes)
        if old.status not in (Status.APPROVED.value, Status.STALE.value):
            raise InvalidDecisionError(f"{chosen.supersedes} is {old.status}: nothing to supersede")
        versions.append(ledger.transition(session, record_id=old.record_id, to=Status.SUPERSEDED))
    versions += [
        ledger.transition(session, record_id=r.record_id, to=Status.SUPERSEDED)
        for r in oc.pending
        if r.record_id != chosen.record_id
    ]
    row = ReviewDecision(
        decision_id=decision_id,
        tenant_id=tenant_id,
        case_id=oc.case.case_id,
        decision="approve",
        decided_by=user_id,
        reason=reason,
        approved_record_id=chosen.record_id,
        record_ids=[v.record_id for v in versions],
        authority=authority,
        enforcement=final_enforcement,
        policy_revision=policy_revision,
    )
    return _close(session, oc, row, versions)


def reject(
    session: Session,
    *,
    tenant_id: str,
    oc: OpenCase,
    user_id: str,
    reason: str,
    policy_revision: str | None,
) -> Decided:
    _require_open(oc)
    decision_id = new_record_id()
    _decision_record(
        session,
        tenant_id=tenant_id,
        decision_id=decision_id,
        oc=oc,
        user_id=user_id,
        body={"decision": "reject", "reason": reason},
    )
    versions = [
        ledger.transition(session, record_id=r.record_id, to=Status.DEPRECATED) for r in oc.pending
    ]
    row = ReviewDecision(
        decision_id=decision_id,
        tenant_id=tenant_id,
        case_id=oc.case.case_id,
        decision="reject",
        decided_by=user_id,
        reason=reason,
        record_ids=[v.record_id for v in versions],
        policy_revision=policy_revision,
    )
    return _close(session, oc, row, versions)


class NotRetirableError(ValueError):
    pass


@dataclass(frozen=True)
class Retired:
    decision_id: str
    version: MemoryRecord


def retire(
    session: Session,
    *,
    tenant_id: str,
    record_id: str,
    user_id: str,
    reason: str,
    policy_revision: str | None,
) -> Retired:
    """The named owner withdraws an approved record: a `deprecated` version
    (outcome `retired`, it was approved once) and a `kind=decision` record of
    who and why. Verify then holds every pack that relied on it (issue 21)."""
    current = ledger.latest(session, record_id)
    if current.status not in (Status.APPROVED.value, Status.STALE.value):
        raise NotRetirableError(
            f"only an approved record can be retired; this one is {current.status}"
        )
    decision_id = new_record_id()
    ledger.append_new(
        session,
        tenant_id=tenant_id,
        record_id=decision_id,
        status=Status.APPROVED,
        approved_by={"approver_type": "user", "user_id": user_id, "decision_id": decision_id},
        draft=ledger.RecordDraft(
            kind=Kind.DECISION,
            schema_version=DECISION_SCHEMA,
            body={"decision": "retire", "record_id": record_id, "reason": reason},
            scope=current.scope,
            asserted_by={"type": "user", "id": user_id},
            authority=0,
            source={"retired_record": record_id, "policy_revision": policy_revision},
            valid_from=dt.datetime.now(dt.UTC),
            entities=tuple(current.entities),
            assurance_level="reviewed",
            derived_from=(record_id,),
            sensitivity={"class": current.sensitivity_class},
        ),
    )
    version = ledger.transition(session, record_id=record_id, to=Status.DEPRECATED)
    return Retired(decision_id, version)
