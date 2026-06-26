"""Immutable policy versions (PART 2 #12).

Revision ID: 202606260003
Revises: 202606260002
Create Date: 2026-06-26

  * policies gains current_version / current_digest / archived (a pointer to the
    latest immutable version + a non-destructive archive flag).
  * policy_versions: append-only frozen snapshots with content digest and
    evaluator version, so a past decision can be reproduced.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260003"
down_revision = "202606260002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policies",
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("policies", sa.Column("current_digest", sa.String(64), nullable=True))
    op.add_column(
        "policies",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "policy_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("policy_type", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("evaluator_version", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "version", name="uq_policy_version"),
    )
    op.create_index("ix_policy_versions_policy_id", "policy_versions", ["policy_id"])
    op.create_index("ix_policy_versions_workspace_id", "policy_versions", ["workspace_id"])
    op.create_index("ix_policy_version_policy", "policy_versions", ["policy_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_policy_version_policy", table_name="policy_versions")
    op.drop_index("ix_policy_versions_workspace_id", table_name="policy_versions")
    op.drop_index("ix_policy_versions_policy_id", table_name="policy_versions")
    op.drop_table("policy_versions")

    op.drop_column("policies", "archived")
    op.drop_column("policies", "current_digest")
    op.drop_column("policies", "current_version")
