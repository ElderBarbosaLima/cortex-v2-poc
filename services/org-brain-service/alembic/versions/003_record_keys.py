"""Per-record data keys (Q10: KEK per tenant in KMS, DEK per lineage root).

- memory_record.key_ref: the lineage root whose data key seals the body. A new
  column on an append-only table is DDL, not a row change; rows written before
  this migration keep NULL (they are plain-json/1, opened without a key).
- record_key: one wrapped data key per (tenant, lineage root). It is outside
  the ledger and the one place ledger-side rows are deleted: deleting a row is
  crypto-shredding that record and everything derived from it (issue 24). No
  UPDATE grant: a key is never rewritten. Under RLS like every tenant table.

Revision ID: 003_record_keys
Revises: 002_search_projection
Create Date: 2026-11-21
"""

from alembic import op

revision = "003_record_keys"
down_revision = "002_search_projection"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE memory_record ADD COLUMN key_ref uuid")
    op.execute(
        """
        CREATE TABLE record_key (
            id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id   uuid        NOT NULL,
            key_ref     uuid        NOT NULL,
            wrapped_dek bytea       NOT NULL,
            kek_alias   text        NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_record_key_ref UNIQUE (tenant_id, key_ref)
        )
        """
    )
    op.execute("ALTER TABLE record_key ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE record_key FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON record_key "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON record_key TO {APP_ROLE}")
    # The view selects `*`, so it must be rebuilt to expose the new column.
    op.execute("DROP VIEW memory_record_current")
    op.execute(
        """
        CREATE VIEW memory_record_current WITH (security_invoker = true) AS
        SELECT DISTINCT ON (record_id) *
        FROM memory_record
        ORDER BY record_id, version DESC
        """
    )
    op.execute(f"GRANT SELECT ON memory_record_current TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS record_key")
    op.execute("DROP VIEW memory_record_current")
    op.execute("ALTER TABLE memory_record DROP COLUMN key_ref")
    op.execute(
        "CREATE VIEW memory_record_current WITH (security_invoker = true) AS "
        "SELECT DISTINCT ON (record_id) * FROM memory_record ORDER BY record_id, version DESC"
    )
    op.execute(f"GRANT SELECT ON memory_record_current TO {APP_ROLE}")
