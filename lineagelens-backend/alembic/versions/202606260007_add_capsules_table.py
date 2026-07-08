"""Evidence Capsule index table (PART 5 #51).

Revision ID: 202606260007
Revises: 202606260006
Create Date: 2026-06-26

The capsule zip itself is stored on disk (storage_ref); this table is the
auditable index of every capsule build (workspace, variant, signed digest).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260007"
down_revision = "202606260006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_capsules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_ref", sa.Uuid(), nullable=False, unique=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("variant", sa.String(32), nullable=False, server_default="full_internal"),
        sa.Column("capsule_digest", sa.String(64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("signature", sa.String(256), nullable=False),
        sa.Column("public_key_id", sa.String(64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("date_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("storage_ref", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_evidence_capsules_public_ref", "evidence_capsules", ["public_ref"], unique=True)
    op.create_index("ix_evidence_capsules_workspace", "evidence_capsules", ["workspace_id"])
    op.create_index("ix_evidence_capsules_digest", "evidence_capsules", ["capsule_digest"])


def downgrade() -> None:
    op.drop_index("ix_evidence_capsules_digest", table_name="evidence_capsules")
    op.drop_index("ix_evidence_capsules_workspace", table_name="evidence_capsules")
    op.drop_index("ix_evidence_capsules_public_ref", table_name="evidence_capsules")
    op.drop_table("evidence_capsules")
