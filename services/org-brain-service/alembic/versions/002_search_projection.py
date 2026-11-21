"""Search projection and tenant registry.

- tenant: the tenants this service holds data for (ids only, no RLS: it holds
  no tenant data). The outbox drainer iterates it, because the outbox itself
  is under RLS and is drained one tenant at a time — never with a bypass role.
- search_doc: the search projection (D12, Rev. B rule 2). One row per record,
  the latest version the projector has seen. It is NOT the system of record:
  `make reindex` deletes and rebuilds it from the ledger, and every hit is
  revalidated against the ledger by the access gate. Its status may lag the
  ledger on purpose (acceptance scenario 4).
  Indexes: pg_bigm (Japanese substrings), a `simple` tsvector (codes, ASCII
  words), pgvector vector(8) for the fixed test vectors only (no embeddings
  in the PoC).

Revision ID: 002_search_projection
Revises: 001_ledger
Create Date: 2026-11-21
"""

from alembic import op

revision = "002_search_projection"
down_revision = "001_ledger"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant (
            tenant_id  uuid PRIMARY KEY,
            key        text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON tenant TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE search_doc (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id   uuid        NOT NULL,
            record_id   uuid        NOT NULL,
            version     smallint    NOT NULL,
            kind        text        NOT NULL,
            status      text        NOT NULL,
            scope       uuid        NOT NULL,
            entities    text[]      NOT NULL DEFAULT '{}',
            valid_from  timestamptz NOT NULL,
            valid_to    timestamptz,
            search_text text        NOT NULL,
            tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED,
            embedding   vector(8),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_search_doc_record UNIQUE (tenant_id, record_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_search_doc_bigm ON search_doc USING gin (search_text gin_bigm_ops)")
    op.execute("CREATE INDEX ix_search_doc_tsv ON search_doc USING gin (tsv)")
    op.execute("ALTER TABLE search_doc ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE search_doc FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON search_doc "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    # A projection is rebuildable, so it is mutable (unlike the ledger).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON search_doc TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_doc")
    op.execute("DROP TABLE IF EXISTS tenant")
