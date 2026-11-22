"""Served context packs: what Cortex told an agent, kept for replay and audit.

- served_pack: one row per pack served (pack_id is a ULID-UUID), with the
  trace id, consumer, end user, purpose, scope, domain, business date
  (`as_of`), OPA's policy revision and the sha256 of the case (the case
  itself is not kept: it is the client's data, not Cortex memory).
- served_pack_item: the `(record_id, version)` pairs in the pack with their
  role, and a snapshot of each body sealed under the record's own data key
  (`key_ref`), so erasing a record's lineage also shreds every pack copy.
Both append-only (trigger + ORM guard) and under RLS; the app may SELECT and
INSERT only.

Revision ID: 006_served_packs
Revises: 005_review_decisions
Create Date: 2026-11-21
"""

from alembic import op

revision = "006_served_packs"
down_revision = "005_review_decisions"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
TABLES = ("served_pack", "served_pack_item")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE served_pack (
            id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            pack_id         uuid        NOT NULL UNIQUE,
            tenant_id       uuid        NOT NULL,
            trace_id        text        NOT NULL,
            consumer_id     text        NOT NULL,
            end_user_id     uuid,
            purpose         text        NOT NULL,
            scope           uuid        NOT NULL,
            domain          text        NOT NULL,
            as_of           date        NOT NULL,
            policy_revision text,
            case_hash       text        NOT NULL CHECK (case_hash ~ '^[0-9a-f]{64}$'),
            served_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE served_pack_item (
            id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id     uuid        NOT NULL,
            pack_id       uuid        NOT NULL REFERENCES served_pack (pack_id),
            position      smallint    NOT NULL,
            record_id     uuid        NOT NULL,
            version       smallint    NOT NULL,
            kind          text        NOT NULL,
            enforcement   text,
            role          text        NOT NULL
                CHECK (role IN ('constraint', 'applied', 'overridden', 'candidate')),
            overridden_by uuid,
            body_hash     text        NOT NULL,
            body_sealed   bytea       NOT NULL,
            body_codec    text        NOT NULL,
            key_ref       uuid        NOT NULL,
            CONSTRAINT uq_served_pack_item UNIQUE (pack_id, position)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION served_pack_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_mutation BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION served_pack_append_only()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION served_pack_append_only()"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS served_pack_append_only()")
