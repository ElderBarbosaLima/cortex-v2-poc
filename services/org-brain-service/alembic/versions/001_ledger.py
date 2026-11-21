"""Ledger: memory_record versions, current view, outbox, RLS, append-only.

- memory_record: one row per immutable version of a record (Rev. B envelope).
  Checks enforce the closed vocabularies, the authority ladder (0–40) and that
  a rule always carries its enforcement.
- memory_record_current: the highest version of each record.
- outbox: projection/job events written in the same transaction (issue 10).
- Row-level security, ENABLEd and FORCEd on both tables, so it binds the table
  owner too. The policy reads app.tenant_id, set per transaction by the
  service; when it is unset every table reads as empty.
- Append-only: a BEFORE UPDATE OR DELETE row trigger and a BEFORE TRUNCATE
  statement trigger on memory_record (a trigger, not REVOKE: the owner cannot
  be revoked from — same reasoning as the monorepo's 014_journal_hardening).
- org_brain_app gets SELECT/INSERT on memory_record (never UPDATE/DELETE),
  and SELECT/INSERT/UPDATE on outbox (its status moves).

Revision ID: 001_ledger
Revises:
Create Date: 2026-11-21
"""

from alembic import op

revision = "001_ledger"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_record (
            id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            record_id         uuid        NOT NULL,
            version           smallint    NOT NULL CHECK (version >= 1),
            tenant_id         uuid        NOT NULL,
            kind              text        NOT NULL
                CHECK (kind IN ('fact', 'rule', 'decision', 'content')),
            schema_version    text        NOT NULL,
            body_sealed       bytea       NOT NULL,
            body_codec        text        NOT NULL,
            body_hash         text        NOT NULL CHECK (body_hash ~ '^[0-9a-f]{64}$'),
            entities          text[]      NOT NULL DEFAULT '{}',
            scope             uuid        NOT NULL,
            source            jsonb       NOT NULL,
            asserted_by       jsonb       NOT NULL,
            approved_by       jsonb,
            status            text        NOT NULL
                CHECK (status IN ('proposed', 'approved', 'stale', 'deprecated',
                                  'superseded', 'erased')),
            enforcement       text
                CHECK (enforcement IN ('mandatory', 'prohibition', 'default')),
            authority         smallint    NOT NULL CHECK (authority BETWEEN 0 AND 40),
            assurance_level   text        NOT NULL
                CHECK (assurance_level IN ('unverified', 'reviewed', 'attested')),
            valid_from        timestamptz NOT NULL,
            valid_to          timestamptz,
            recorded_at       timestamptz NOT NULL DEFAULT now(),
            supersedes        uuid,
            derived_from      uuid[]      NOT NULL DEFAULT '{}',
            sensitivity_class text        NOT NULL
                CHECK (sensitivity_class IN ('public', 'internal', 'confidential',
                                             'restricted')),
            sensitivity       jsonb       NOT NULL,
            CONSTRAINT uq_memory_record_version UNIQUE (record_id, version),
            CONSTRAINT ck_rule_has_enforcement CHECK (kind <> 'rule' OR enforcement IS NOT NULL),
            CONSTRAINT ck_validity_window CHECK (valid_to IS NULL OR valid_to > valid_from),
            CONSTRAINT ck_approved_has_approver
                CHECK (status <> 'approved' OR approved_by IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_memory_record_tenant_record "
        "ON memory_record (tenant_id, record_id, version DESC)"
    )
    op.execute(
        "CREATE INDEX ix_memory_record_applicability "
        "ON memory_record (tenant_id, kind, enforcement, scope)"
    )
    op.execute("CREATE INDEX ix_memory_record_entities ON memory_record USING gin (entities)")

    op.execute(
        """
        CREATE VIEW memory_record_current WITH (security_invoker = true) AS
        SELECT DISTINCT ON (record_id) *
        FROM memory_record
        ORDER BY record_id, version DESC
        """
    )

    op.execute(
        """
        CREATE TABLE outbox (
            id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id         uuid        NOT NULL,
            event_type        text        NOT NULL,
            payload           jsonb       NOT NULL,
            subject_record_id uuid,
            status            text        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'dispatched', 'skipped', 'failed')),
            attempt_count     smallint    NOT NULL DEFAULT 0,
            last_error        text,
            created_at        timestamptz NOT NULL DEFAULT now(),
            claimed_at        timestamptz,
            dispatched_at     timestamptz
        )
        """
    )
    op.execute("CREATE INDEX ix_outbox_status_created_at ON outbox (status, created_at)")

    for table in ("memory_record", "outbox"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )

    op.execute(
        """
        CREATE FUNCTION memory_record_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'memory_record is append-only; append a new version instead'
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER memory_record_no_mutation BEFORE UPDATE OR DELETE ON memory_record "
        "FOR EACH ROW EXECUTE FUNCTION memory_record_append_only()"
    )
    op.execute(
        "CREATE TRIGGER memory_record_no_truncate BEFORE TRUNCATE ON memory_record "
        "FOR EACH STATEMENT EXECUTE FUNCTION memory_record_append_only()"
    )

    op.execute(f"GRANT SELECT, INSERT ON memory_record TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON memory_record_current TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON outbox TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS memory_record_current")
    op.execute("DROP TABLE IF EXISTS outbox")
    op.execute("DROP TABLE IF EXISTS memory_record")
    op.execute("DROP FUNCTION IF EXISTS memory_record_append_only()")
