"""Verifications: every release check of an output against its served pack.

One row per `POST /v1/cortex/verify`: pass or hold, the reasons, the sha256 of
the output checked (the output itself is the client's data), the trace id and
the policy revision in force. Append-only (trigger + ORM guard), under RLS;
the app may SELECT and INSERT only. Linked to `served_pack` by pack_id.

Revision ID: 007_verifications
Revises: 006_served_packs
Create Date: 2026-11-21
"""

from alembic import op

revision = "007_verifications"
down_revision = "006_served_packs"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE verification (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            verification_id uuid        NOT NULL UNIQUE,
            tenant_id       uuid        NOT NULL,
            pack_id         uuid        NOT NULL REFERENCES served_pack (pack_id),
            trace_id        text        NOT NULL,
            consumer_id     text        NOT NULL,
            end_user_id     uuid,
            result          text        NOT NULL CHECK (result IN ('pass', 'hold')),
            reasons         jsonb       NOT NULL,
            output_hash     text        NOT NULL CHECK (output_hash ~ '^[0-9a-f]{64}$'),
            policy_revision text,
            verified_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_hold_has_a_reason
                CHECK (result = 'pass' OR jsonb_array_length(reasons) > 0)
        )
        """
    )
    op.execute(
        "CREATE TRIGGER verification_no_mutation BEFORE UPDATE OR DELETE ON verification "
        "FOR EACH ROW EXECUTE FUNCTION served_pack_append_only()"
    )
    op.execute(
        "CREATE TRIGGER verification_no_truncate BEFORE TRUNCATE ON verification "
        "FOR EACH STATEMENT EXECUTE FUNCTION served_pack_append_only()"
    )
    op.execute("ALTER TABLE verification ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE verification FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON verification "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT ON verification TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification")
