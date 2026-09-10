"""Add Frontier encryption, notarization, and policy-version schema.

Revision ID: 202609090001
Revises: 202609080001
Create Date: 2026-09-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202609090001"
down_revision = "202609080001"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_UUID = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")


def upgrade() -> None:
    op.add_column(
        "provenance_records",
        sa.Column("is_encrypted", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.add_column(
        "retention_policies",
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "routing_policies",
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
    )

    op.create_table(
        "retention_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("retain_days", sa.Integer(), nullable=False),
        sa.Column("redact_after_days", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("policy_id", "version", name="uq_retention_policy_version"),
    )
    op.create_index("ix_retention_policy_versions_policy_id", "retention_policy_versions", ["policy_id"])
    op.create_index("ix_retention_policy_versions_workspace_id", "retention_policy_versions", ["workspace_id"])
    op.create_index("ix_retention_policy_version_policy", "retention_policy_versions", ["policy_id", "version"])

    op.create_table(
        "routing_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("policy_id", _UUID, nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("mappings", _JSON, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("policy_id", "version", name="uq_routing_policy_version"),
    )
    op.create_index("ix_routing_policy_versions_policy_id", "routing_policy_versions", ["policy_id"])
    op.create_index("ix_routing_policy_versions_workspace_id", "routing_policy_versions", ["workspace_id"])
    op.create_index("ix_routing_policy_version_policy", "routing_policy_versions", ["policy_id", "version"])

    op.create_table(
        "record_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_uuid", sa.String(64), nullable=False),
        sa.Column("encryption_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("record_uuid", name="uq_record_keys_record_uuid"),
    )
    op.create_index("ix_record_keys_record_uuid", "record_keys", ["record_uuid"])

    op.create_table(
        "epoch_notarizations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("merkle_root", sa.String(64), nullable=False),
        sa.Column("start_record_uuid", sa.String(64), nullable=True),
        sa.Column("end_record_uuid", sa.String(64), nullable=True),
        sa.Column("tsa_token", sa.Text(), nullable=False),
        sa.Column("tsa_provider", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_epoch_notarizations_workspace_id", "epoch_notarizations", ["workspace_id"])
    op.create_index("ix_epoch_notarization_workspace_root", "epoch_notarizations", ["workspace_id", "merkle_root"])


def downgrade() -> None:
    op.drop_index("ix_epoch_notarization_workspace_root", table_name="epoch_notarizations")
    op.drop_index("ix_epoch_notarizations_workspace_id", table_name="epoch_notarizations")
    op.drop_table("epoch_notarizations")

    op.drop_index("ix_record_keys_record_uuid", table_name="record_keys")
    op.drop_table("record_keys")

    op.drop_index("ix_routing_policy_version_policy", table_name="routing_policy_versions")
    op.drop_index("ix_routing_policy_versions_workspace_id", table_name="routing_policy_versions")
    op.drop_index("ix_routing_policy_versions_policy_id", table_name="routing_policy_versions")
    op.drop_table("routing_policy_versions")

    op.drop_index("ix_retention_policy_version_policy", table_name="retention_policy_versions")
    op.drop_index("ix_retention_policy_versions_workspace_id", table_name="retention_policy_versions")
    op.drop_index("ix_retention_policy_versions_policy_id", table_name="retention_policy_versions")
    op.drop_table("retention_policy_versions")

    op.drop_column("routing_policies", "current_version")
    op.drop_column("retention_policies", "current_version")
    op.drop_column("provenance_records", "is_encrypted")
