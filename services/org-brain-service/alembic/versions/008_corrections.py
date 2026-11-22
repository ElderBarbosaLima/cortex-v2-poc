"""Corrections: a reviewer's correction of an agent's output (writeback, doc 12 flow 4).

One row per accepted correction, keyed by the caller's idempotency key (unique
per tenant): a retry with the same key returns the same result and writes
nothing. It links the served pack, the work case (`case_ref`), the person who
corrected it, the `kind=decision` record that holds the correction as memory,
and, when the D17 thresholds were met, the proposed rule and its review case.
`conflict_key` groups corrections about the same thing for the thresholds
(distinct cases, distinct correctors, within a window). `request_hash` detects
a reused key with a different request. Append-only, under RLS; the app may
SELECT and INSERT only.

Revision ID: 008_corrections
Revises: 007_verifications
Create Date: 2026-11-22
"""

from alembic import op

revision = "008_corrections"
down_revision = "007_verifications"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE correction (
            id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            correction_id      uuid        NOT NULL UNIQUE,
            tenant_id          uuid        NOT NULL,
            idempotency_key    text        NOT NULL,
            request_hash       text        NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            pack_id            uuid        NOT NULL REFERENCES served_pack (pack_id),
            case_ref           text        NOT NULL,
            corrected_by       uuid        NOT NULL,
            conflict_key       text        NOT NULL,
            decision_record_id uuid        NOT NULL,
            proposed_record_id uuid,
            review_case_id     uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_correction_idempotency UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_correction_conflict ON correction (tenant_id, conflict_key, created_at)"
    )
    op.execute(
        "CREATE TRIGGER correction_no_mutation BEFORE UPDATE OR DELETE ON correction "
        "FOR EACH ROW EXECUTE FUNCTION served_pack_append_only()"
    )
    op.execute(
        "CREATE TRIGGER correction_no_truncate BEFORE TRUNCATE ON correction "
        "FOR EACH STATEMENT EXECUTE FUNCTION served_pack_append_only()"
    )
    op.execute("ALTER TABLE correction ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE correction FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON correction "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT ON correction TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS correction")
