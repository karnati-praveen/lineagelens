"""Provenance continuity drill results table (PART 5 #55).

Revision ID: 202606260008
Revises: 202606260007
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260008"
down_revision = "202606260007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "continuity_drills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_ref", sa.Uuid(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("overall_status", sa.String(16), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("signature", sa.String(256), nullable=True),
        sa.Column("public_key_id", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_continuity_drills_public_ref", "continuity_drills", ["public_ref"], unique=True)
    op.create_index("ix_continuity_drills_workspace", "continuity_drills", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_continuity_drills_workspace", table_name="continuity_drills")
    op.drop_index("ix_continuity_drills_public_ref", table_name="continuity_drills")
    op.drop_table("continuity_drills")
