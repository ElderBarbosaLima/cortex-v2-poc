"""Writeback (issue 22, doc 12 flow 4): a reviewer's correction becomes memory.

1. The correction is recorded as a `kind=decision` record (what the reviewer
   decided about this case), `derived_from` the records in the served pack.
2. The domain pack turns it into a rule draft (`DomainPack.correction_to_rule`).
3. D17 thresholds decide whether the draft becomes a proposal yet:
   at least `min_distinct_cases` work cases and `min_distinct_correctors`
   people corrected the same thing (same conflict key) within `window_days`.
   Demo default 1/1/30 (one correction is one proposal); the golden set also
   runs 3/2/30.
4. The proposal goes through the admission gate like any other (issue 17),
   asserted by `policy` (authority capped at the model ceiling until a person
   approves), with every corrector listed as a contributor: none of them may
   approve the resulting rule (separation of duties, always on).
The idempotency key makes a retry a no-op that returns the first result.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain_packs.base import DomainPack
from app.models import Correction, Kind, Status
from app.services import admission, ledger
from app.services.access_gate.applicability import CaseFacts
from app.services.access_gate.context_pack import StoredPack
from app.services.admission import SENSITIVITY_RANK
from app.services.body_codec import body_hash
from app.utils.business_dates import business_start
from app.utils.ids import new_record_id

SUGGESTER = {"type": "policy", "id": "d17-rule-suggestion"}


@dataclass(frozen=True)
class RuleSuggestionPolicy:
    min_distinct_cases: int = 1
    min_distinct_correctors: int = 1
    window_days: int = 30

    @classmethod
    def from_spec(cls, spec: str) -> RuleSuggestionPolicy:
        """`"cases/correctors/days"`, e.g. "1/1/30" or "3/2/30"."""
        cases, correctors, days = (int(p) for p in spec.split("/"))
        if min(cases, correctors, days) < 1:
            raise ValueError(f"rule suggestion thresholds must be >= 1: {spec!r}")
        return cls(cases, correctors, days)


class IdempotencyConflictError(ValueError):
    """The key was used before for a different request."""


class InvalidCorrectionError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    distinct_cases: int
    distinct_correctors: int
    met: bool
    case_refs: tuple[str, ...]
    correctors: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    correction: Correction
    evidence: Evidence | None  # None on an idempotent replay
    problems: tuple[admission.Problem, ...] = ()
    replayed: bool = False


def request_hash(payload: Mapping[str, Any]) -> str:
    return body_hash(dict(payload))


def find(
    session: Session, *, tenant_id: str, idempotency_key: str, payload_hash: str
) -> Correction | None:
    """The earlier result for this key. The transaction-scoped advisory lock
    serialises concurrent retries of one key, so the second waits and then
    finds the first's row instead of writing a duplicate."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 22))"),
        {"k": f"{tenant_id}:{idempotency_key}"},
    )
    row = session.scalars(
        select(Correction).where(Correction.idempotency_key == idempotency_key)
    ).first()
    if row is not None and row.request_hash != payload_hash:
        raise IdempotencyConflictError(
            "this idempotency key was already used for a different correction"
        )
    return row


def _evidence(
    session: Session, *, key: str, policy: RuleSuggestionPolicy, case_ref: str, corrector: str
) -> Evidence:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=policy.window_days)
    rows = session.execute(
        select(Correction.case_ref, Correction.corrected_by).where(
            Correction.conflict_key == key, Correction.created_at >= since
        )
    ).all()
    cases = sorted({r.case_ref for r in rows} | {case_ref})
    people = sorted({str(r.corrected_by) for r in rows} | {corrector})
    met = len(cases) >= policy.min_distinct_cases and len(people) >= policy.min_distinct_correctors
    return Evidence(len(cases), len(people), met, tuple(cases), tuple(people))


def _decision(
    session: Session,
    *,
    stored: StoredPack,
    facts: CaseFacts,
    case_ref: str,
    corrector: str,
    correction: BaseModel,
) -> str:
    decision_id = new_record_id()
    classes = ["internal"] + [
        ledger.latest(session, item.record_id).sensitivity_class for item, _ in stored.items
    ]
    ledger.append_new(
        session,
        tenant_id=stored.pack.tenant_id,
        record_id=decision_id,
        status=Status.APPROVED,
        approved_by={"approver_type": "user", "user_id": corrector, "decision_id": decision_id},
        draft=ledger.RecordDraft(
            kind=Kind.DECISION,
            schema_version="cortex.correction/1",
            body={
                "case_ref": case_ref,
                "pack_id": stored.pack.pack_id,
                "correction": correction.model_dump(mode="json"),
            },
            scope=stored.pack.scope,
            asserted_by={"type": "user", "id": corrector},
            authority=0,  # episodic memory: it outranks nothing
            source={"case_ref": case_ref, "pack_id": stored.pack.pack_id},
            valid_from=dt.datetime.now(dt.UTC),
            entities=facts.entities,
            assurance_level="reviewed",
            derived_from=tuple(item.record_id for item, _ in stored.items),
            sensitivity={"class": max(classes, key=SENSITIVITY_RANK.__getitem__)},
        ),
    )
    return decision_id


def submit(
    session: Session,
    *,
    stored: StoredPack,
    pack: DomainPack,
    facts: CaseFacts,
    case: Mapping[str, Any],
    case_ref: str,
    corrector: str,
    raw_correction: Mapping[str, Any],
    idempotency_key: str,
    payload_hash: str,
    policy: RuleSuggestionPolicy,
) -> Outcome:
    try:
        correction = pack.correction_schema.model_validate(raw_correction)
    except ValidationError as exc:
        raise InvalidCorrectionError(str(exc.errors()[0].get("msg", exc))) from None
    draft = pack.correction_to_rule(correction, case=case)
    proposal = admission.Proposal(
        domain=pack.name,
        kind=Kind.RULE,
        schema_version=f"{pack.name}.rule/1",
        body=dict(draft.body),
        scope=stored.pack.scope,
        entities=draft.entities,
        enforcement=draft.enforcement,
        authority=min(draft.authority, ledger.MODEL_AUTHORITY_CEILING),
        sensitivity={"class": "internal", "personal_data": False},
        # The reviewer judged this case as of its business date, so the rule
        # holds from that date (Q8: business time, never the clock).
        valid_from=business_start(stored.pack.as_of),
    )
    key = admission.conflict_key(proposal)
    decision_id = _decision(
        session,
        stored=stored,
        facts=facts,
        case_ref=case_ref,
        corrector=corrector,
        correction=correction,
    )
    evidence = _evidence(session, key=key, policy=policy, case_ref=case_ref, corrector=corrector)
    proposed_id: str | None = None
    case_id: str | None = None
    problems: tuple[admission.Problem, ...] = ()
    if evidence.met:
        proposal = dataclasses.replace(
            proposal,
            source={
                "case_ref": case_ref,
                "case_refs": list(evidence.case_refs),
                "contributors": list(evidence.correctors),
                "rule_suggestion": f"{policy.min_distinct_cases}/"
                f"{policy.min_distinct_correctors}/{policy.window_days}",
            },
            derived_from=(decision_id,),
        )
        _, found = admission.validate_proposal(proposal)
        problems = tuple(found)
        if not problems:
            admitted = admission.admit(
                session, tenant_id=stored.pack.tenant_id, proposal=proposal, asserted_by=SUGGESTER
            )
            proposed_id, case_id = admitted.record.record_id, admitted.review_case_id
    row = Correction(
        correction_id=new_record_id(),
        tenant_id=stored.pack.tenant_id,
        idempotency_key=idempotency_key,
        request_hash=payload_hash,
        pack_id=stored.pack.pack_id,
        case_ref=case_ref,
        corrected_by=corrector,
        conflict_key=key,
        decision_record_id=decision_id,
        proposed_record_id=proposed_id,
        review_case_id=case_id,
    )
    session.add(row)
    session.flush()
    return Outcome(row, evidence, problems)
