"""The search projection interface (D12).

The ledger is the only system of record; every index is a projection fed from
the outbox and rebuildable from the ledger. A projection only ever proposes
*candidates*: the access gate revalidates each one against the ledger, and
mandatory rules and prohibitions never come from search at all (Rev. B rule 4).
An Elasticsearch adapter would be one more implementation of this protocol,
touching neither the ledger nor the gates.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SearchFilters:
    kinds: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()  # readable scope node ids (from the access decision)
    statuses: tuple[str, ...] = ("approved",)
    business_time: dt.datetime | None = None


@dataclass(frozen=True)
class Candidate:
    record_id: str
    version: int  # the version the projection saw; may lag the ledger
    score: float


@dataclass(frozen=True)
class SearchQuery:
    text: str
    vector: tuple[float, ...] | None = None  # fixed test vectors only (no embeddings in the PoC)
    limit: int = 20
    filters: SearchFilters = field(default_factory=SearchFilters)


class SearchProjection(Protocol):
    def apply(self, session: Session, payload: dict[str, Any]) -> None:
        """Bring the projection up to date with one `record.appended` event."""
        ...

    def rebuild(self, session: Session, tenant_id: str) -> int:
        """Drop this tenant's projection and rebuild it from the ledger."""
        ...

    def search(self, session: Session, query: SearchQuery) -> list[Candidate]: ...
