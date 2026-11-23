"""Context packs (issue 20, doc 12 flow 2): what an agent may rely on for one case.

The caller has OPA's read decision (readable scopes, sensitivity ceiling,
policy revision). Then:
1. **Rules** come from applicability (SQL + CEL, issue 19) and resolution.
   Never from search.
2. **Knowledge** (facts, content) comes from the `SearchProjection`, as
   candidates only. Each one is **revalidated against the ledger**: it must
   be in this tenant, currently approved (the projection may lag the outbox),
   valid at `as_of`, at a readable scope and within the sensitivity ceiling.
   The ledger's current version is what goes in the pack, not the version
   the projection saw.
3. The pack is **stored** before it is returned (append-only `served_pack`),
   with each body sealed again under the record's own data key, so a replay
   (`load`) answers exactly what was served without re-running anything, and
   erasing a lineage shreds every copy.

The pack is held, not answered, when a constraint cannot be honoured: a
mandatory rule or prohibition whose condition cannot be evaluated for this
case, or one above the purpose's sensitivity ceiling. Dropping it would let
the agent act without a constraint (Rev. B rule 4).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain_packs.base import DomainPack
from app.models import MemoryRecord, ServedPack, ServedPackItem, Status
from app.projections.base import SearchFilters, SearchProjection, SearchQuery
from app.projections.embeddings import query_vector
from app.schemas.cortex import ContextPack, PackRecord
from app.services import ledger
from app.services.access_gate.applicability import CaseFacts, applicable_rules
from app.services.access_gate.resolve import resolve
from app.services.admission import SENSITIVITY_RANK
from app.services.body_codec import BodyUnreadableError, body_hash, codec_for, default_codec
from app.utils.business_dates import business_start
from app.utils.ids import new_record_id

KNOWLEDGE_KINDS = ("fact", "content")
SEARCH_LIMIT = 20
_ROLE_ORDER = {"constraint": 0, "applied": 1, "overridden": 2}


class PackHeldError(Exception):
    """The pack cannot be served without dropping a constraint."""

    def __init__(self, error: str, detail: Any) -> None:
        super().__init__(f"{error}: {detail}")
        self.error = error
        self.detail = detail


@dataclass(frozen=True)
class Access:
    readable_scopes: tuple[str, ...]
    sensitivity_ceiling: str | None
    policy_revision: str | None

    def within_ceiling(self, sensitivity_class: str) -> bool:
        ceiling = SENSITIVITY_RANK.get(self.sensitivity_ceiling or "", -1)
        return SENSITIVITY_RANK[sensitivity_class] <= ceiling


@dataclass(frozen=True)
class PackRequest:
    tenant_id: str
    consumer_id: str
    end_user_id: str | None
    trace_id: str
    domain: str
    case: Mapping[str, Any]
    scope: str
    purpose: str
    as_of: dt.date
    query: str | None = None


@dataclass(frozen=True)
class _Item:
    row: MemoryRecord
    body: dict[str, Any]
    role: str
    overridden_by: str | None = None


@dataclass(frozen=True)
class Dropped:
    record_id: str
    reason: str


def revalidate(
    session: Session, record_id: str, *, tenant_id: str, access: Access, at: dt.datetime
) -> MemoryRecord | Dropped:
    """A search hit is only a candidate until the ledger agrees."""
    try:
        row = ledger.latest(session, record_id)
    except ledger.RecordNotFoundError:
        return Dropped(record_id, "not_in_ledger")
    checks = [
        (row.tenant_id == tenant_id, "other_tenant"),
        (row.status == Status.APPROVED.value, f"status_{row.status}"),
        (row.kind in KNOWLEDGE_KINDS, f"kind_{row.kind}"),
        (ledger.is_valid_at(row, at), "not_valid_at_as_of"),
        (row.scope in access.readable_scopes, "scope_not_readable"),
        (access.within_ceiling(row.sensitivity_class), "above_sensitivity_ceiling"),
    ]
    for ok, reason in checks:
        if not ok:
            return Dropped(record_id, reason)
    return row


def _rules(
    session: Session, *, pack: DomainPack, facts: CaseFacts, access: Access, as_of: dt.date
) -> list[_Item]:
    result = applicable_rules(
        session, pack=pack, facts=facts, readable_scopes=access.readable_scopes, as_of=as_of
    )
    blocked = [u for u in result.unevaluable if u.enforcement != "default"]
    if blocked:
        raise PackHeldError(
            "unevaluable_constraint",
            [{"record_id": u.record_id, "code": u.code, "detail": u.detail} for u in blocked],
        )
    hidden = [
        r.record_id
        for r in result.rules
        if r.enforcement != "default" and not access.within_ceiling(r.row.sensitivity_class)
    ]
    if hidden:
        raise PackHeldError(
            "constraint_above_ceiling",
            f"constraints {hidden} apply to this case but are above the purpose's "
            f"{access.sensitivity_ceiling} ceiling",
        )
    items = [
        _Item(r.rule.row, r.rule.body.model_dump(), r.role, r.overridden_by)
        for r in resolve(result.rules)
        if access.within_ceiling(r.rule.row.sensitivity_class)  # a hidden default just goes
    ]
    return sorted(items, key=lambda i: (_ROLE_ORDER[i.role], i.row.record_id))


def _knowledge(
    session: Session,
    *,
    search: SearchProjection,
    request: PackRequest,
    pack: DomainPack,
    access: Access,
) -> tuple[list[_Item], list[Dropped]]:
    at = business_start(request.as_of)
    text = " ".join(t for t in (pack.search_text(request.case), request.query or "") if t)
    hits = search.search(
        session,
        SearchQuery(
            text=text,
            vector=query_vector(text),
            limit=SEARCH_LIMIT,
            filters=SearchFilters(
                kinds=KNOWLEDGE_KINDS, scopes=access.readable_scopes, business_time=at
            ),
        ),
    )
    items: list[_Item] = []
    dropped: list[Dropped] = []
    for hit in hits:
        checked = revalidate(
            session, hit.record_id, tenant_id=request.tenant_id, access=access, at=at
        )
        if isinstance(checked, Dropped):
            dropped.append(checked)
        else:
            items.append(_Item(checked, ledger.open_body(session, checked), "candidate"))
    return items, dropped


@dataclass(frozen=True)
class Built:
    pack_id: str
    dropped: list[Dropped]


def build_and_store(
    session: Session,
    *,
    request: PackRequest,
    pack: DomainPack,
    facts: CaseFacts,
    access: Access,
    search: SearchProjection,
) -> Built:
    rules = _rules(session, pack=pack, facts=facts, access=access, as_of=request.as_of)
    knowledge, dropped = _knowledge(
        session, search=search, request=request, pack=pack, access=access
    )
    pack_id = new_record_id()
    session.add(
        ServedPack(
            pack_id=pack_id,
            tenant_id=request.tenant_id,
            trace_id=request.trace_id,
            consumer_id=request.consumer_id,
            end_user_id=request.end_user_id,
            purpose=request.purpose,
            scope=request.scope,
            domain=request.domain,
            as_of=request.as_of,
            policy_revision=access.policy_revision,
            case_hash=body_hash(dict(request.case)),
        )
    )
    session.flush()
    codec = default_codec()
    for position, item in enumerate([*rules, *knowledge]):
        key_ref = item.row.key_ref or item.row.record_id
        session.add(
            ServedPackItem(
                tenant_id=request.tenant_id,
                pack_id=pack_id,
                position=position,
                record_id=item.row.record_id,
                version=item.row.version,
                kind=item.row.kind,
                enforcement=item.row.enforcement,
                role=item.role,
                overridden_by=item.overridden_by,
                body_hash=item.row.body_hash,
                body_sealed=codec.seal(
                    session, tenant_id=request.tenant_id, key_ref=key_ref, body=item.body
                ),
                body_codec=codec.name,
                key_ref=key_ref,
            )
        )
    session.flush()
    return Built(pack_id, dropped)


@dataclass(frozen=True)
class StoredPack:
    pack: ServedPack
    items: Sequence[tuple[ServedPackItem, dict[str, Any] | None]]  # None: erased since


class PackNotFoundError(LookupError):
    pass


def load(session: Session, pack_id: str) -> StoredPack:
    """The pack exactly as served: stored rows, bodies opened from the snapshot.
    Nothing is re-run. Another tenant's pack is invisible (RLS)."""
    served = session.scalars(select(ServedPack).where(ServedPack.pack_id == pack_id)).first()
    if served is None:
        raise PackNotFoundError(pack_id)
    rows = session.scalars(
        select(ServedPackItem)
        .where(ServedPackItem.pack_id == pack_id)
        .order_by(ServedPackItem.position)
    ).all()
    return StoredPack(served, [(row, _open_item(session, row)) for row in rows])


def _open_item(session: Session, row: ServedPackItem) -> dict[str, Any] | None:
    """The served body, or None once the record's key was erased (issue 24):
    the pack stays whole (ids, versions, roles, body_hash), that body does not."""
    try:
        return codec_for(row.body_codec).open(
            session, tenant_id=row.tenant_id, key_ref=row.key_ref, sealed=row.body_sealed
        )
    except BodyUnreadableError:
        return None


def render(stored: StoredPack) -> ContextPack:
    """The pack as the API serves it, from storage: POST, GET and the evidence
    manifest all render it here, so they cannot drift apart."""
    served = stored.pack
    return ContextPack(
        pack_id=uuid.UUID(served.pack_id),
        trace_id=served.trace_id,
        domain=served.domain,
        scope=uuid.UUID(served.scope),
        purpose=served.purpose,
        as_of=served.as_of,
        policy_revision=served.policy_revision,
        served_at=served.served_at,
        records=[
            PackRecord(
                record_id=uuid.UUID(item.record_id),
                version=item.version,
                kind=item.kind,
                enforcement=item.enforcement,
                role=item.role,  # type: ignore[arg-type]
                overridden_by=uuid.UUID(item.overridden_by) if item.overridden_by else None,
                body_hash=item.body_hash,
                body=body,
                body_erased=body is None,
            )
            for item, body in stored.items
        ],
    )
