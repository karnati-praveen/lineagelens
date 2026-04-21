"""Ensure role column exists and backfill any NULL values.

Handles databases that were created before migration 202501160001 was introduced,
or where the ADD COLUMN ran but server_default did not backfill existing rows.

Revision ID: 202501170001
Revises: 202501160001
Create Date: 2025-01-17 00:01:00
"""

from __future__ import annotations

from alembic import op


revision = "202501170001"
down_revision = "202501160001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD COLUMN IF NOT EXISTS is a no-op when role already exists,
    # so this is safe to run regardless of which upgrade path was taken.
    op.execute("""
        ALTER TABLE user_accounts
        ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'member'
    """)

    # Backfill any rows where role ended up NULL or empty despite the default.
    op.execute("""
        UPDATE user_accounts
        SET role = 'member'
        WHERE role IS NULL OR TRIM(role) = ''
    """)


def downgrade() -> None:
    pass
