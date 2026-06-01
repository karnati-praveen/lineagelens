"""Add provenance_records hash chain columns

Revision ID: 202606010001
Revises: 202605270001
Create Date: 2026-06-01

Adds:
- provenance_records.record_hash  (nullable String(64))
  SHA-256 hex of this record's canonical fields.  NULL on Lite (solo) mode
  and on records written before this migration.

- provenance_records.prev_hash  (nullable String(64))
  SHA-256 hex of the preceding record in the same workspace, forming a
  tamper-evident append-only chain.  NULL for the first record in a workspace
  or when record_hash is NULL.

Both columns are nullable so existing rows and Lite-mode instances are
unaffected.  The chain is written only when BACKEND_MODE != 'solo'.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606010001"
down_revision = "202605270001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provenance_records",
        sa.Column("record_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column("prev_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_provenance_record_hash",
        "provenance_records",
        ["record_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provenance_record_hash", table_name="provenance_records")
    op.drop_column("provenance_records", "prev_hash")
    op.drop_column("provenance_records", "record_hash")
