"""What each served pack was built with (issue 37, scenario 5).

A pack requested again after a model or index change is the stored one
(GET replays it); a fresh POST is a new pack. Each records what built it, so
nobody can mistake one for the other: the search projection's version and
the embedding model in use (null when embeddings were off).

Revision ID: 012_pack_built_with
Revises: 011_any_dimension_embeddings
Create Date: 2026-11-23
"""

from alembic import op

revision = "012_pack_built_with"
down_revision = "011_any_dimension_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE served_pack ADD COLUMN projection text")
    op.execute("ALTER TABLE served_pack ADD COLUMN embedding_model text")


def downgrade() -> None:
    op.execute("ALTER TABLE served_pack DROP COLUMN embedding_model")
    op.execute("ALTER TABLE served_pack DROP COLUMN projection")
