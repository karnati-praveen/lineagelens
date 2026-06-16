"""Add leads table for VS Code extension email capture.

Revision ID: 202606150001
Revises: 202606140002
Create Date: 2026-06-15

Stores optional, user-provided emails from the VS Code extension for product
update emails.  Email is unique + normalized (lowercased) so upserts are safe.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606150001"
down_revision = "202606140002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("extension_version", sa.String(32), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_leads_email"),
    )
    op.create_index("ix_leads_email", "leads", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_leads_email", table_name="leads")
    op.drop_table("leads")
