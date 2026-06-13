"""Add incidents and incident_integrations tables

Revision ID: 202606120001
Revises: 202606010001
Create Date: 2026-06-12

Adds:
- incidents: production-incident records linked to AI provenance data.
  Stores affected file paths, timeline, and optional external-source metadata.

- incident_integrations: per-workspace webhook configuration for incident
  intake (encrypted webhook_secret, HMAC-verified on ingestion).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606120001"
down_revision = "202606010001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("affected_files", sa.JSON(), nullable=False),
        sa.Column("external_source", sa.String(64), nullable=True),
        sa.Column("external_ref", sa.String(256), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid", name="uq_incident_uuid"),
    )
    op.create_index("ix_incident_uuid", "incidents", ["uuid"])
    op.create_index("ix_incident_workspace_id", "incidents", ["workspace_id"])
    op.create_index("ix_incident_workspace_started", "incidents", ["workspace_id", "started_at"])

    op.create_table(
        "incident_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("webhook_secret", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_incident_integration_workspace"),
    )
    op.create_index("ix_incident_integration_workspace", "incident_integrations", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_incident_integration_workspace", table_name="incident_integrations")
    op.drop_table("incident_integrations")
    op.drop_index("ix_incident_workspace_started", table_name="incidents")
    op.drop_index("ix_incident_workspace_id", table_name="incidents")
    op.drop_index("ix_incident_uuid", table_name="incidents")
    op.drop_table("incidents")
