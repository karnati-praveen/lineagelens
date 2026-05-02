"""Add refresh token jti for refresh-token rotation.

Revision ID: 202501200001
Revises: 202501190001
Create Date: 2025-01-20 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "202501200001"
down_revision: str | None = "202501190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_accounts", sa.Column("refresh_token_jti", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("user_accounts", "refresh_token_jti")
