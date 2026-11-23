"""`make reindex`: delete and rebuild every tenant's search projection from the ledger.

    python -m app.cli.reindex

Proves the projection is only a projection (D12 acceptance criterion a).
"""

from __future__ import annotations

from app.clients.llm import configure as configure_llm
from app.config import OrgBrainConfig
from app.db import app_engine, tenant_session
from app.keys import install as install_keys
from app.projections.postgres_search import PostgresSearchProjection
from app.workers.outbox import tenant_ids


def main() -> None:
    engine = app_engine()
    install_keys(tenant_ids(engine))
    configure_llm(OrgBrainConfig())  # embeddings through LiteLLM, when on (issue 30)
    projection = PostgresSearchProjection()
    for tenant in tenant_ids(engine):
        with tenant_session(tenant, engine) as session:
            count = projection.rebuild(session, tenant)
        print(f"reindexed tenant {tenant}: {count} records")


if __name__ == "__main__":
    main()
