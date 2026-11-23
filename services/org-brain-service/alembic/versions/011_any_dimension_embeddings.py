"""Embeddings of any dimension in the search projection (issue 30).

`search_doc.embedding` was `vector(8)`, for the fixed test vectors only. Real
embeddings through LiteLLM have the model's dimension (1536 for
text-embedding-ada-002), so the column becomes a plain `vector`. Search only
compares vectors of the same dimension. The projection is rebuildable from the
ledger, so nothing of record is at stake.

Revision ID: 011_any_dimension_embeddings
Revises: 010_erasures
Create Date: 2026-11-22
"""

from alembic import op

revision = "011_any_dimension_embeddings"
down_revision = "010_erasures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE search_doc ALTER COLUMN embedding TYPE vector")


def downgrade() -> None:
    op.execute("UPDATE search_doc SET embedding = NULL WHERE vector_dims(embedding) <> 8")
    op.execute("ALTER TABLE search_doc ALTER COLUMN embedding TYPE vector(8)")
