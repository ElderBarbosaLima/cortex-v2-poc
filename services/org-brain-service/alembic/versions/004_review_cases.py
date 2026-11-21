"""Review queue: grouped cases of proposals waiting for a named owner.

- review_case: one open case per (tenant, conflict key). The conflict key is
  derived from the proposal's targets and entities, so many proposals about
  the same thing (e.g. one conflict met by hundreds of jobs, Rev. B scenario 6)
  share one case. Status moves open -> decided (issue 18 records decisions).
  A partial unique index keeps at most one open case per key.
- review_case_item: the proposals attached to a case (record ids).
Both under RLS. The app may SELECT/INSERT, and UPDATE review_case.status only.

Revision ID: 004_review_cases
Revises: 003_record_keys
Create Date: 2026-11-21
"""

from alembic import op

revision = "004_review_cases"
down_revision = "003_record_keys"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE review_case (
            id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            case_id      uuid        NOT NULL UNIQUE,
            tenant_id    uuid        NOT NULL,
            conflict_key text        NOT NULL,
            scope        uuid        NOT NULL,
            title        text        NOT NULL,
            status       text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'decided')),
            opened_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_review_case_open ON review_case (tenant_id, conflict_key) "
        "WHERE status = 'open'"
    )
    op.execute(
        """
        CREATE TABLE review_case_item (
            id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id  uuid        NOT NULL,
            case_id    uuid        NOT NULL REFERENCES review_case (case_id),
            record_id  uuid        NOT NULL,
            added_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_review_case_item UNIQUE (case_id, record_id)
        )
        """
    )
    for table in ("review_case", "review_case_item"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )
    op.execute(f"GRANT SELECT, INSERT ON review_case, review_case_item TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (status) ON review_case TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS review_case_item")
    op.execute("DROP TABLE IF EXISTS review_case")
