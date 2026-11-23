"""A served pack can carry similar past cases (issue 42).

IBM's AI asks Cortex for a customer's approved rules AND how similar payments
were resolved. Past cases are `decision` records (each reviewed outcome); a
pack stores the ones it served, with the role `similar_case`, so a replay
shows exactly what was served and erasure still shreds their bodies.

Revision ID: 014_similar_cases
Revises: 013_machine_proposals_guard
Create Date: 2026-11-23
"""

from alembic import op

revision = "014_similar_cases"
down_revision = "013_machine_proposals_guard"
branch_labels = None
depends_on = None

_OLD = "('constraint', 'applied', 'overridden', 'candidate')"
_NEW = "('constraint', 'applied', 'overridden', 'candidate', 'similar_case')"
_TABLE = "ALTER TABLE served_pack_item"
_NAME = "served_pack_item_role_check"


def upgrade() -> None:
    op.execute(f"{_TABLE} DROP CONSTRAINT {_NAME}")
    op.execute(f"{_TABLE} ADD CONSTRAINT {_NAME} CHECK (role IN {_NEW})")


def downgrade() -> None:
    op.execute(f"{_TABLE} DROP CONSTRAINT {_NAME}")
    op.execute(f"{_TABLE} ADD CONSTRAINT {_NAME} CHECK (role IN {_OLD})")
