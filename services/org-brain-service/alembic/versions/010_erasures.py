"""Erasures: what was erased, when, by whom (issue 24, Q10).

One row per erasure (a record with its descendants, or a whole tenant) with a
report of what was destroyed: record ids, data keys shredded, projection rows
deleted, packs and exports affected. The report never holds body text.
Append-only (trigger + ORM guard), under RLS; the app may SELECT and INSERT.

Revision ID: 010_erasures
Revises: 009_evidence_exports
Create Date: 2026-11-22
"""

from alembic import op

revision = "010_erasures"
down_revision = "009_evidence_exports"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE erasure (
            id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            erasure_id   uuid        NOT NULL UNIQUE,
            tenant_id    uuid        NOT NULL,
            scope        text        NOT NULL CHECK (scope IN ('record', 'tenant')),
            subject      text        NOT NULL,
            requested_by text        NOT NULL,
            reason       text        NOT NULL CHECK (reason <> ''),
            report       jsonb       NOT NULL,
            erased_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE TRIGGER erasure_no_mutation BEFORE UPDATE OR DELETE ON erasure "
        "FOR EACH ROW EXECUTE FUNCTION served_pack_append_only()"
    )
    op.execute(
        "CREATE TRIGGER erasure_no_truncate BEFORE TRUNCATE ON erasure "
        "FOR EACH STATEMENT EXECUTE FUNCTION served_pack_append_only()"
    )
    op.execute("ALTER TABLE erasure ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE erasure FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON erasure "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT ON erasure TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS erasure")
