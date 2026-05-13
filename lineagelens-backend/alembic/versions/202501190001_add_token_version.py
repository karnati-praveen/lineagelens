"""Add token_version to user_accounts for stateless token revocation.

Incrementing token_version on logout or password change makes all previously
issued refresh tokens for that user immediately invalid without requiring a
shared token blacklist store.

Revision ID: 202501190001
Revises: 202501180001
Create Date: 2025-01-19 00:01:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "202501190001"
down_revision = "202501180001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_accounts",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_accounts", "token_version")
