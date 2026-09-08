"""Pairing codes for easy client connection.

Revision ID: 202609080001
Revises: 202606260009
Create Date: 2026-09-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202609080001"
down_revision = "202606260009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pairing_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("client", sa.String(32), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("label", sa.String(256), nullable=True),
        sa.Column(
            "scopes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_redeemed_label", sa.String(256), nullable=True),
    )
    op.create_index("ix_pairing_codes_workspace", "pairing_codes", ["workspace_id"])
    op.create_index("ix_pairing_codes_code_hash", "pairing_codes", ["code_hash"])


def downgrade() -> None:
    op.drop_index("ix_pairing_codes_code_hash", table_name="pairing_codes")
    op.drop_index("ix_pairing_codes_workspace", table_name="pairing_codes")
    op.drop_table("pairing_codes")
