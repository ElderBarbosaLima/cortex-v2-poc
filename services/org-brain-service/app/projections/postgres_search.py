"""Postgres implementation of the search projection: pg_bigm + FTS + pgvector.

Score per document, deterministic (ties broken by record_id):
  number of query terms found as substrings (pg_bigm index, works for Japanese)
  + pg_bigm similarity to the whole query
  + ts_rank on the `simple` tsvector (codes such as PO-88231, ASCII words)
  + cosine similarity to `query.vector`, when both sides have one.
A document is a candidate if any term matches, or the bigram similarity
reaches pg_bigm's default limit (0.3), or the tsquery matches.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import MemoryRecord
from app.projections.base import Candidate, SearchQuery
from app.projections.embeddings import embed as _embed
from app.services import ledger
from app.services.body_codec import BodyUnreadableError

# A meaning-only match (no shared words) must be this close to be a candidate.
# Measured on text-embedding-ada-002 through LiteLLM (make test-llm): related
# Japanese sentences ~0.90, unrelated ones ~0.84. Candidates are still
# revalidated against the ledger before any pack.
MIN_CLOSENESS = 0.88
_UPSERT = text(
    """
    INSERT INTO search_doc (tenant_id, record_id, version, kind, status, scope, entities,
                            valid_from, valid_to, search_text, embedding, updated_at)
    VALUES (:tenant_id, :record_id, :version, :kind, :status, :scope, :entities,
            :valid_from, :valid_to, :search_text, CAST(:embedding AS vector), now())
    ON CONFLICT (tenant_id, record_id) DO UPDATE SET
        version = EXCLUDED.version, kind = EXCLUDED.kind, status = EXCLUDED.status,
        scope = EXCLUDED.scope, entities = EXCLUDED.entities, valid_from = EXCLUDED.valid_from,
        valid_to = EXCLUDED.valid_to, search_text = EXCLUDED.search_text,
        embedding = EXCLUDED.embedding, updated_at = now()
    WHERE search_doc.version <= EXCLUDED.version
    """
)

_SEARCH = text(
    """
    WITH parts AS (
        SELECT d.record_id::text AS record_id, d.version,
               (SELECT count(*) FROM unnest(CAST(:terms AS text[])) AS t(term)
                 WHERE d.search_text LIKE likequery(t.term)) AS hits,
               bigm_similarity(d.search_text, :q) AS sim,
               ts_rank(d.tsv, plainto_tsquery('simple', :q)) AS rank,
               d.tsv @@ plainto_tsquery('simple', :q) AS fts,
               CASE WHEN d.embedding IS NOT NULL AND CAST(:vector AS vector) IS NOT NULL
                         AND vector_dims(d.embedding) = vector_dims(CAST(:vector AS vector))
                    THEN 1 - (d.embedding <=> CAST(:vector AS vector)) ELSE 0 END AS closeness
        FROM search_doc d
        WHERE d.status = ANY(CAST(:statuses AS text[]))
          AND (cardinality(CAST(:kinds AS text[])) = 0 OR d.kind = ANY(CAST(:kinds AS text[])))
          AND (cardinality(CAST(:scopes AS uuid[])) = 0 OR d.scope = ANY(CAST(:scopes AS uuid[])))
          AND (CAST(:t AS timestamptz) IS NULL
               OR (d.valid_from <= CAST(:t AS timestamptz)
                   AND (d.valid_to IS NULL OR CAST(:t AS timestamptz) < d.valid_to)))
    )
    SELECT record_id, version, hits + sim + rank + closeness AS score FROM parts
    WHERE hits > 0 OR fts OR sim >= 0.3 OR closeness >= :min_closeness
    ORDER BY score DESC, record_id
    LIMIT :limit
    """
)


def _search_text(body: dict[str, Any], entities: list[str]) -> str:
    parts = [str(body.get("text_ja", "")), *(str(t) for t in body.get("targets", [])), *entities]
    return " ".join(p for p in parts if p)


def _vector_literal(values: Any) -> str | None:
    if not values:
        return None
    return "[" + ",".join(str(float(v)) for v in values) + "]"


class PostgresSearchProjection:
    version = "postgres-search/2"  # pg_bigm + FTS + pgvector of any dimension (migration 011)

    def _drop(self, session: Session, row: MemoryRecord) -> None:
        session.execute(
            text("DELETE FROM search_doc WHERE tenant_id = :t AND record_id = :r"),
            {"t": row.tenant_id, "r": row.record_id},
        )

    def _upsert(self, session: Session, row: MemoryRecord) -> None:
        """Index the current version, or drop it: an erased record, or one whose
        key is gone, never has searchable text (issue 24: reindex must not bring
        erased text back)."""
        if row.status == "erased":
            self._drop(session, row)
            return
        try:
            body = ledger.open_body(session, row)
        except BodyUnreadableError:
            self._drop(session, row)
            return
        session.execute(
            _UPSERT,
            {
                "tenant_id": row.tenant_id,
                "record_id": row.record_id,
                "version": row.version,
                "kind": row.kind,
                "status": row.status,
                "scope": row.scope,
                "entities": list(row.entities),
                "valid_from": row.valid_from,
                "valid_to": row.valid_to,
                "search_text": (search_text := _search_text(body, list(row.entities))),
                "embedding": _vector_literal(body.get("test_vector") or _embed(search_text)),
            },
        )

    def apply(self, session: Session, payload: dict[str, Any]) -> None:
        # Read the ledger, not the payload: an event replayed late must not
        # move the projection backwards (the upsert also refuses older versions).
        self._upsert(session, ledger.latest(session, str(payload["record_id"])))

    def rebuild(self, session: Session, tenant_id: str) -> int:
        session.execute(text("DELETE FROM search_doc WHERE tenant_id = :t"), {"t": tenant_id})
        rows = session.scalars(
            select(MemoryRecord)
            .where(MemoryRecord.tenant_id == tenant_id)
            .order_by(MemoryRecord.record_id, MemoryRecord.version.desc())
        ).all()
        latest: dict[str, MemoryRecord] = {}
        for row in rows:
            latest.setdefault(row.record_id, row)  # first seen = highest version
        current = list(latest.values())
        for row in current:
            self._upsert(session, row)
        return len(current)

    def search(self, session: Session, query: SearchQuery) -> list[Candidate]:
        f = query.filters
        rows = session.execute(
            _SEARCH,
            {
                "q": query.text,
                "terms": [t for t in query.text.split() if t],
                "vector": _vector_literal(query.vector),
                "statuses": list(f.statuses),
                "kinds": list(f.kinds),
                "scopes": list(f.scopes),
                "t": f.business_time,
                "min_closeness": MIN_CLOSENESS,
                "limit": query.limit,
            },
        ).all()
        return [
            Candidate(record_id=r.record_id, version=int(r.version), score=float(r.score))
            for r in rows
        ]
