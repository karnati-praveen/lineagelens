"""External witness receipts table (PART 5 #53).

Revision ID: 202606260009
Revises: 202606260008
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260009"
down_revision = "202606260008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "witness_receipts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("root_hash", sa.String(64), nullable=False),
        sa.Column("backend", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_witness_receipts_workspace", "witness_receipts", ["workspace_id"])
    op.create_index("ix_witness_receipts_root", "witness_receipts", ["root_hash"])
    op.create_index(
        "ix_witness_receipts_workspace_root", "witness_receipts", ["workspace_id", "root_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_witness_receipts_workspace_root", table_name="witness_receipts")
    op.drop_index("ix_witness_receipts_root", table_name="witness_receipts")
    op.drop_index("ix_witness_receipts_workspace", table_name="witness_receipts")
    op.drop_table("witness_receipts")
