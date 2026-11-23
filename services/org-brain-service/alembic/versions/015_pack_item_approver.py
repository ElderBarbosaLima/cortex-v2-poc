"""Who approved each served version (issue 43).

IBM's audit trail records, for every match, the rules applied with their ID,
version and approver (slide 22). The approver of a version never changes, so
the pack stores it when served: a replay cites the same person.

Revision ID: 015_pack_item_approver
Revises: 014_similar_cases
Create Date: 2026-11-23
"""

from alembic import op

revision = "015_pack_item_approver"
down_revision = "014_similar_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE served_pack_item ADD COLUMN approved_by uuid")


def downgrade() -> None:
    op.execute("ALTER TABLE served_pack_item DROP COLUMN approved_by")
