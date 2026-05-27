"""Add dynamic routing: routing_policies table and routing_decision column

Revision ID: 202605260001
Revises: 202501240001
Create Date: 2026-05-27

Adds:
- routing_policies table (one row per workspace + provider)
- provenance_records.routing_decision JSON column (nullable)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202605260001"
down_revision = "202501240001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── routing_policies table ─────────────────────────────────────────────
    op.create_table(
        "routing_policies",
        sa.Column(
            "id",
            sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column(
            "mappings",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "provider",
            name="uq_routing_policy_workspace_provider",
        ),
    )
    op.create_index(
        "ix_routing_policy_workspace",
        "routing_policies",
        ["workspace_id"],
    )

    # ── provenance_records.routing_decision column ─────────────────────────
    op.add_column(
        "provenance_records",
        sa.Column(
            "routing_decision",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("provenance_records", "routing_decision")
    op.drop_index("ix_routing_policy_workspace", table_name="routing_policies")
    op.drop_table("routing_policies")
