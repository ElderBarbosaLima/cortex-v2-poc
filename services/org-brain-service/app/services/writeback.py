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
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.clients.llm import LlmUnavailable, rule_suggester
from app.domain_packs.base import DomainPack
from app.models import Correction, Kind, Status
from app.services import admission, ledger
from app.services.access_gate.applicability import CaseFacts
from app.services.access_gate.context_pack import StoredPack
from app.services.admission import SENSITIVITY_RANK
from app.services.body_codec import body_hash
from app.utils.business_dates import NO_LOWER_BOUND, business_start
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
    suggested_by: str | None = None  # "policy" (the pack's draft) or "model" (LiteLLM)


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


_LLM_SYSTEM = """You turn a reviewer's correction into ONE rule for a governed memory.
Answer with ONLY a JSON object:
{"text_ja": str, "targets": [str], "condition": str, "entities": [str]}.
- text_ja: one sentence saying what must be done, for whom. Write it in the SAME language as
  the baseline draft's text_ja (English if that is English, Japanese if Japanese), whatever the
  field is called.
- condition: a CEL boolean over the variables and fields listed; integers only, dates as
  date("YYYY-MM-DD"); it must hold for the corrected case and stay as narrow as the correction.
- targets: only from the list given.
- entities: canonical keys only (e.g. customer:CUST-0042), never a name. If the condition compares
  an entity field to a literal, that entity must be listed.
A baseline draft is given; keep its meaning, improve it only where the correction says more."""


def _llm_proposal(
    proposal: admission.Proposal,
    *,
    pack: DomainPack,
    correction: BaseModel,
    case: Mapping[str, Any],
) -> tuple[admission.Proposal | None, tuple[admission.Problem, ...], str | None]:
    """The model's draft through LiteLLM, admitted only if the gate accepts it.
    Returns (proposal or None, problems, model). Off or failing: (None, …)."""
    client = rule_suggester()
    if client is None:
        return None, (), None
    brief = {
        "domain": pack.name,
        "condition_variables": {v: sorted(f) for v, f in pack.condition_fields.items()},
        "entity_fields": dict(pack.entity_fields),
        "targets": sorted(pack.targets),
        "correction": correction.model_dump(mode="json"),
        "case": dict(case),
        "baseline": {
            "text_ja": proposal.body.get("text_ja"),
            "targets": proposal.body.get("targets"),
            "condition": proposal.body.get("condition"),
            "entities": list(proposal.entities),
        },
    }
    try:
        answer = client.chat_json(
            system=_LLM_SYSTEM, user=json.dumps(brief, ensure_ascii=False, default=str)
        )
        body = {
            **proposal.body,
            "text_ja": str(answer["text_ja"]),
            "targets": [str(t) for t in answer["targets"]],
            "condition": str(answer["condition"]),
        }
        entities = tuple(sorted({str(e) for e in answer.get("entities", [])}))
    except LlmUnavailable as exc:
        return None, (admission.Problem("llm_unavailable", str(exc)),), client.chat_model
    except (KeyError, TypeError) as exc:
        return None, (admission.Problem("llm_malformed", f"missing {exc}"),), client.chat_model
    drafted = dataclasses.replace(proposal, body=body, entities=entities)
    _, problems = admission.validate_proposal(drafted)
    if problems:
        return (
            None,
            tuple(admission.Problem(f"llm_{p.code}", p.detail) for p in problems),
            client.chat_model,
        )
    return drafted, (), client.chat_model


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
    ancestors: Sequence[str] = (),
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
        # No lower bound (issue 25): a correction teaches a requirement that
        # already existed, and nobody knows since when, so the learned rule
        # also applies to cases dated before the corrected one.
        valid_from=business_start(NO_LOWER_BOUND),
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
    suggested_by: str | None = None
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
        drafted, llm_problems, model = _llm_proposal(
            proposal, pack=pack, correction=correction, case=case
        )
        asserted_by: dict[str, Any] = SUGGESTER
        if drafted is not None:
            proposal = dataclasses.replace(
                drafted, source={**proposal.source, "suggested_by": "model", "model": model}
            )
            asserted_by = {"type": "model", "id": str(model)}
        _, found = admission.validate_proposal(proposal)
        # A suggestion is a machine's: it may not loosen an inherited constraint (issue 31).
        found += admission.inherited_conflicts(session, proposal, ancestors)
        problems = (*llm_problems, *found)
        if not found:
            admitted = admission.admit(
                session, tenant_id=stored.pack.tenant_id, proposal=proposal, asserted_by=asserted_by
            )
            proposed_id, case_id = admitted.record.record_id, admitted.review_case_id
            suggested_by = "model" if drafted is not None else "policy"
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
    return Outcome(row, evidence, problems, suggested_by=suggested_by)
