"""Widen provenance_records.license_status for honest match states (PART 1 #2).

Revision ID: 202606260002
Revises: 202606260001
Create Date: 2026-06-26

The new states (not_configured / insufficient_corpus / clean_within_corpus /
review / match / scan_error) no longer fit String(16). SQLite ignores VARCHAR
length, so this matters only for Postgres.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260002"
down_revision = "202606260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "provenance_records",
        "license_status",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "provenance_records",
        "license_status",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=True,
    )
