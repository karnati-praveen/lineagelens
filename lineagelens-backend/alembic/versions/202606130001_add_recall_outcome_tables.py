"""Add recall_campaigns, record_outcomes tables; quarantine columns on provenance_records

Revision ID: 202606130001
Revises: 202606120001
Create Date: 2026-06-13

F2 — AI Code Recall:
  * recall_campaigns: tracks a recall campaign (match criteria, open/closed status).
  * provenance_records: quarantine_status, quarantine_reason, quarantine_recall_id,
    quarantined_at columns.

F3 — Outcome-Calibrated Trust:
  * record_outcomes: per-record fate events (survived, reverted, rewritten_by_human,
    test_failed, incident_linked, review_flagged) with source and observed_at.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606130001"
down_revision = "202606120001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── F2: recall_campaigns ─────────────────────────────────────────────────
    op.create_table(
        "recall_campaigns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("criteria_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recall_campaign_workspace", "recall_campaigns", ["workspace_id"])

    # ── F2: quarantine columns on provenance_records ─────────────────────────
    op.add_column(
        "provenance_records",
        sa.Column("quarantine_status", sa.String(32), nullable=False, server_default="active"),
    )
    op.add_column(
        "provenance_records",
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column("quarantine_recall_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_provenance_quarantine_status",
        "provenance_records",
        ["quarantine_status"],
    )
    op.create_index(
        "ix_provenance_quarantine_recall_id",
        "provenance_records",
        ["quarantine_recall_id"],
    )

    # ── F3: record_outcomes ──────────────────────────────────────────────────
    op.create_table(
        "record_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("record_uuid", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("outcome_type", sa.String(32), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_record_outcome_workspace_uuid",
        "record_outcomes",
        ["workspace_id", "record_uuid"],
    )
    op.create_index(
        "ix_record_outcome_workspace_type",
        "record_outcomes",
        ["workspace_id", "outcome_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_record_outcome_workspace_type", table_name="record_outcomes")
    op.drop_index("ix_record_outcome_workspace_uuid", table_name="record_outcomes")
    op.drop_table("record_outcomes")

    op.drop_index("ix_provenance_quarantine_recall_id", table_name="provenance_records")
    op.drop_index("ix_provenance_quarantine_status", table_name="provenance_records")
    op.drop_column("provenance_records", "quarantined_at")
    op.drop_column("provenance_records", "quarantine_recall_id")
    op.drop_column("provenance_records", "quarantine_reason")
    op.drop_column("provenance_records", "quarantine_status")

    op.drop_index("ix_recall_campaign_workspace", table_name="recall_campaigns")
    op.drop_table("recall_campaigns")
