"""Add workspaces table.

Revision ID: 202501220001
Revises: 202501210001
Create Date: 2026-05-17 00:00:01.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202501220001"
down_revision: str | None = "202501210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW_SQL = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workspaces_owner_id", "workspaces", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_workspaces_owner_id", table_name="workspaces")
    op.drop_table("workspaces")
