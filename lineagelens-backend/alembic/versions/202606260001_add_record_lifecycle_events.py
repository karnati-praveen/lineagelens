"""Add record lifecycle events + content-commitment columns (PART 2 #10 & #11).

Revision ID: 202606260001
Revises: 202606150001
Create Date: 2026-06-26

Non-destructive privacy lifecycle for provenance records:
  * provenance_records gains prompt_sha256 / content_sha256 (content commitments
    captured at hash-chain time) and lifecycle_state (active|redacted|deleted).
  * record_lifecycle_events: signed, append-only redaction/deletion tombstone
    events so the verifier reports validly_redacted / validly_deleted instead of
    a false "tampered", and chain linkage survives erasure.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260001"
down_revision = "202606150001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── content-commitment + lifecycle columns on provenance_records ──────────
    op.add_column(
        "provenance_records",
        sa.Column("prompt_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column("content_sha256", sa.String(64), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column(
            "lifecycle_state",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
    )
    op.create_index(
        "ix_provenance_lifecycle_state",
        "provenance_records",
        ["lifecycle_state"],
    )

    # ── record_lifecycle_events ──────────────────────────────────────────────
    op.create_table(
        "record_lifecycle_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_ref", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("record_uuid", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("policy_ref", sa.String(256), nullable=True),
        sa.Column("authorized_by", sa.String(128), nullable=True),
        sa.Column("content_commitment", sa.JSON(), nullable=False),
        sa.Column("statement_json", sa.Text(), nullable=False),
        sa.Column("signature", sa.String(256), nullable=False),
        sa.Column("public_key_id", sa.String(64), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_ref", name="uq_lifecycle_event_public_ref"),
    )
    op.create_index(
        "ix_record_lifecycle_events_public_ref",
        "record_lifecycle_events",
        ["public_ref"],
    )
    op.create_index(
        "ix_record_lifecycle_events_workspace_id",
        "record_lifecycle_events",
        ["workspace_id"],
    )
    op.create_index(
        "ix_record_lifecycle_events_record_uuid",
        "record_lifecycle_events",
        ["record_uuid"],
    )
    op.create_index(
        "ix_lifecycle_event_workspace_record",
        "record_lifecycle_events",
        ["workspace_id", "record_uuid"],
    )


def downgrade() -> None:
    op.drop_index("ix_lifecycle_event_workspace_record", table_name="record_lifecycle_events")
    op.drop_index("ix_record_lifecycle_events_record_uuid", table_name="record_lifecycle_events")
    op.drop_index("ix_record_lifecycle_events_workspace_id", table_name="record_lifecycle_events")
    op.drop_index("ix_record_lifecycle_events_public_ref", table_name="record_lifecycle_events")
    op.drop_table("record_lifecycle_events")

    op.drop_index("ix_provenance_lifecycle_state", table_name="provenance_records")
    op.drop_column("provenance_records", "lifecycle_state")
    op.drop_column("provenance_records", "content_sha256")
    op.drop_column("provenance_records", "prompt_sha256")
