"""Add provenance_records.confidence_breakdown column

Revision ID: 202605270001
Revises: 202502280001
Create Date: 2026-05-27

Adds:
- provenance_records.confidence_breakdown  (nullable JSON/JSONB)
  Holds the list of EvidenceItem dicts produced by the confidence engine.
  NULL for records ingested before this migration — no backfill.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202605270001"
down_revision = "202605260001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provenance_records",
        sa.Column(
            "confidence_breakdown",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("provenance_records", "confidence_breakdown")
