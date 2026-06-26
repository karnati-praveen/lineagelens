"""Freeze recall membership + blast-radius coverage (PART 2 #13 & #14).

Revision ID: 202606260004
Revises: 202606260003
Create Date: 2026-06-26

recall_campaigns gains a frozen, signed membership snapshot (member_uuids +
digest + signature) and the frozen blast-radius set with its coverage status,
so quarantine acts on the set captured at open time instead of re-running the
criteria (which could drift).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260004"
down_revision = "202606260003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recall_campaigns",
        sa.Column("criteria_version", sa.String(16), nullable=False, server_default="1"),
    )
    op.add_column("recall_campaigns", sa.Column("member_uuids", sa.JSON(), nullable=True))
    op.add_column("recall_campaigns", sa.Column("member_digest", sa.String(64), nullable=True))
    op.add_column("recall_campaigns", sa.Column("member_signature", sa.String(256), nullable=True))
    op.add_column("recall_campaigns", sa.Column("member_public_key_id", sa.String(64), nullable=True))
    op.add_column("recall_campaigns", sa.Column("blast_uuids", sa.JSON(), nullable=True))
    op.add_column("recall_campaigns", sa.Column("blast_coverage_status", sa.String(32), nullable=True))
    op.add_column("recall_campaigns", sa.Column("graph_checkpoint", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("recall_campaigns", "graph_checkpoint")
    op.drop_column("recall_campaigns", "blast_coverage_status")
    op.drop_column("recall_campaigns", "blast_uuids")
    op.drop_column("recall_campaigns", "member_public_key_id")
    op.drop_column("recall_campaigns", "member_signature")
    op.drop_column("recall_campaigns", "member_digest")
    op.drop_column("recall_campaigns", "member_uuids")
    op.drop_column("recall_campaigns", "criteria_version")
