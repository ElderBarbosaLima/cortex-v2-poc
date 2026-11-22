"""Search projections (D12): derived, rebuildable, never the system of record."""

from __future__ import annotations

from app.projections.base import SearchProjection


def default_projection() -> SearchProjection:
    """The search adapter this deployment uses. Only the projections package
    names the concrete one, so swapping it (e.g. for Elasticsearch) touches
    nothing outside this package (D12)."""
    from app.projections.postgres_search import PostgresSearchProjection

    return PostgresSearchProjection()
