"""Embeddings for the search projection, through LiteLLM when that use is on
(issue 30). Adapter-neutral: any `SearchProjection` and the context pack use it.
No embedding is never a failure: records are still found by their words, and
every candidate is revalidated against the ledger.
"""

from __future__ import annotations

import structlog

from app.clients.llm import LlmUnavailable, embedder

_logger = structlog.get_logger("org-brain.embeddings")


def embed(text: str) -> list[float] | None:
    client = embedder()
    if client is None or not text:
        return None
    try:
        return client.embed([text])[0]
    except LlmUnavailable as exc:
        _logger.warning("embedding.skipped", error=str(exc))
        return None


def query_vector(text: str) -> tuple[float, ...] | None:
    """The query's embedding for `SearchQuery.vector`, when embeddings are on."""
    vector = embed(text)
    return tuple(vector) if vector else None
