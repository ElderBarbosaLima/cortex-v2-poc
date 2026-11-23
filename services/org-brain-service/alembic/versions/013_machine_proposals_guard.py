"""Machines propose, people approve, enforced by the database (issue 34).

Nake's review: model-proposed rules go through the review queue and never
write the ledger directly. Decision taken for the PoC (the likely path, Rev. B
rule 6): proposals stay in the ledger as `proposed` (invisible to every pack,
which reads only approved records), so rejected ones stay auditable, and the
database refuses the two ways a machine could skip review:
- a record asserted by a `model` or a `policy` must start as `proposed`;
- it can become `approved` only with `approved_by.approver_type = user`.
The application already does both; this makes it impossible to bypass.

Revision ID: 013_machine_proposals_guard
Revises: 012_pack_built_with
Create Date: 2026-11-23
"""

from alembic import op

revision = "013_machine_proposals_guard"
down_revision = "012_pack_built_with"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION memory_record_machine_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.asserted_by->>'type' IN ('model', 'policy') THEN
                IF NEW.version = 1 AND NEW.status <> 'proposed' THEN
                    RAISE EXCEPTION 'a % record starts as proposed (review queue first)',
                        NEW.asserted_by->>'type' USING ERRCODE = 'check_violation';
                END IF;
                IF NEW.status = 'approved'
                   AND COALESCE(NEW.approved_by->>'approver_type', '') <> 'user' THEN
                    RAISE EXCEPTION 'a % record is approved only by a person',
                        NEW.asserted_by->>'type' USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER memory_record_machine_guard BEFORE INSERT ON memory_record "
        "FOR EACH ROW EXECUTE FUNCTION memory_record_machine_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS memory_record_machine_guard ON memory_record")
    op.execute("DROP FUNCTION IF EXISTS memory_record_machine_guard()")
