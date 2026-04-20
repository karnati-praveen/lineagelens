"""Add role to user_accounts and user_id to provenance_records.

Revision ID: 202501160001
Revises: 202501150001
Create Date: 2025-01-16 00:01:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202501160001"
down_revision = "202501150001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_accounts",
        sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
    )
    op.add_column(
        "provenance_records",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_provenance_user_id", "provenance_records", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_provenance_user_id", table_name="provenance_records")
    op.drop_column("provenance_records", "user_id")
    op.drop_column("user_accounts", "role")
