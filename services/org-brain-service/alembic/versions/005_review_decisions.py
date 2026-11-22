"""Review decisions: who approved or rejected what, append-only.

- review_decision: one row per decision on a case (approve | reject), with the
  named person who decided, the reason, the final authority/enforcement on an
  approval and the policy revision OPA decided under. Append-only (trigger, as
  memory_record): a decision is never edited, a later one is a new row. Its
  `decision_id` is also the record_id of the `kind=decision` ledger record
  that carries the decision as memory, and of `approved_by.decision_id`.
- review_case.title is dropped. It held the start of a proposal's `text_ja` in
  clear, outside the sealed body, so erasing a lineage (crypto-shredding,
  Q10) would have left that text behind. The title is now read from the
  sealed body of the case's first proposal.
Both under RLS. The app may SELECT/INSERT review_decision, nothing else.

Revision ID: 005_review_decisions
Revises: 004_review_cases
Create Date: 2026-11-21
"""

from alembic import op

revision = "005_review_decisions"
down_revision = "004_review_cases"
branch_labels = None
depends_on = None

APP_ROLE = "org_brain_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE review_case DROP COLUMN title")
    op.execute(
        """
        CREATE TABLE review_decision (
            id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            decision_id        uuid        NOT NULL UNIQUE,
            tenant_id          uuid        NOT NULL,
            case_id            uuid        NOT NULL REFERENCES review_case (case_id),
            decision           text        NOT NULL CHECK (decision IN ('approve', 'reject')),
            decided_by         uuid        NOT NULL,
            reason             text,
            approved_record_id uuid,
            record_ids         uuid[]      NOT NULL,
            authority          smallint    CHECK (authority BETWEEN 0 AND 40),
            enforcement        text
                CHECK (enforcement IN ('mandatory', 'prohibition', 'default')),
            policy_revision    text,
            decided_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_approval_names_its_record
                CHECK (decision <> 'approve' OR approved_record_id IS NOT NULL),
            CONSTRAINT ck_rejection_has_a_reason
                CHECK (decision <> 'reject' OR coalesce(reason, '') <> '')
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_decision_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'review_decision is append-only'
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER review_decision_no_mutation BEFORE UPDATE OR DELETE ON review_decision "
        "FOR EACH ROW EXECUTE FUNCTION review_decision_append_only()"
    )
    op.execute(
        "CREATE TRIGGER review_decision_no_truncate BEFORE TRUNCATE ON review_decision "
        "FOR EACH STATEMENT EXECUTE FUNCTION review_decision_append_only()"
    )
    op.execute("ALTER TABLE review_decision ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE review_decision FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON review_decision "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT ON review_decision TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS review_decision")
    op.execute("DROP FUNCTION IF EXISTS review_decision_append_only()")
    op.execute("ALTER TABLE review_case ADD COLUMN title text NOT NULL DEFAULT ''")
