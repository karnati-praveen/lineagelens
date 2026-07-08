"""DB-backed attestation key registry (PART 5 #57).

Revision ID: 202606260006
Revises: 202606260005
Create Date: 2026-06-26

core.attestation's key registry (PART 3 #19) was env-only (ATTESTATION_KEY_
REGISTRY), so revoking a compromised key required a redeploy. This table lets
an admin revoke/register keys at runtime; the env registry remains supported
as a fallback for air-gapped deployments with no admin API access.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260006"
down_revision = "202606260005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attestation_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_key_id", sa.String(64), nullable=False, unique=True),
        sa.Column("public_key_hex", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compromised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("revoked_by", sa.String(128), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_attestation_keys_public_key_id", "attestation_keys", ["public_key_id"], unique=True)
    op.create_index("ix_attestation_keys_status", "attestation_keys", ["status"])


def downgrade() -> None:
    op.drop_index("ix_attestation_keys_status", table_name="attestation_keys")
    op.drop_index("ix_attestation_keys_public_key_id", table_name="attestation_keys")
    op.drop_table("attestation_keys")
