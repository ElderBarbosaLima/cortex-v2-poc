"""Evidence exports: a served pack's manifest, archived to the evidence bucket.

One row per pack (pack_id is unique: exporting again is the same export). The
request writes the row and an outbox event in one transaction; the worker
builds the manifest, encrypts it under the tenant's key, puts it under a
deterministic object key with Object Lock retention and records the result.
Status moves pending -> exported (or failed); the app may UPDATE only those
result columns. Under RLS.

Revision ID: 009_evidence_exports
Revises: 008_corrections
Create Date: 2026-11-22
"""

from alembic import op

revision = "009_evidence_exports"
down_revision = "008_corrections"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evidence_export (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            export_id       uuid        NOT NULL UNIQUE,
            tenant_id       uuid        NOT NULL,
            pack_id         uuid        NOT NULL UNIQUE REFERENCES served_pack (pack_id),
            bucket          text        NOT NULL,
            object_key      text        NOT NULL,
            status          text        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'exported', 'failed')),
            manifest_sha256 text        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
            version_id      text,
            retention_mode  text,
            retain_until    timestamptz,
            requested_at    timestamptz NOT NULL DEFAULT now(),
            exported_at     timestamptz,
            CONSTRAINT ck_exported_has_a_result CHECK (
                status <> 'exported' OR (manifest_sha256 IS NOT NULL AND exported_at IS NOT NULL)
            )
        )
        """
    )
    op.execute("ALTER TABLE evidence_export ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evidence_export FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON evidence_export "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT ON evidence_export TO {APP_ROLE}")
    op.execute(
        "GRANT UPDATE (status, manifest_sha256, version_id, retention_mode, retain_until, "
        f"exported_at) ON evidence_export TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_export")
