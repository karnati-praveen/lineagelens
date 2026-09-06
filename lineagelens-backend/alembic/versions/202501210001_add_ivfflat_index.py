"""Add IVFFLAT index on embedding_vector for fast vector search at scale.

Without this index, pgvector performs an exact scan of every row.
With it, queries use approximate nearest-neighbour search (ANN), which is
orders of magnitude faster once the table exceeds ~50k records.

Revision ID: 202501210001
Revises: 202501200001
Create Date: 2025-01-21 00:01:00.000000
"""

from alembic import op


revision = "202501210001"
down_revision = "202501200001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # lists=100 is a good starting point for most deployments.
    # Rule of thumb: sqrt(total_rows), re-index when the table grows 10x.
    # Requires at least 3 * lists rows to build; safe to apply on an empty table.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_provenance_embedding_ivfflat
        ON provenance_records
        USING ivfflat (embedding_vector vector_cosine_ops)
        WITH (lists = 100)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_provenance_embedding_ivfflat")
