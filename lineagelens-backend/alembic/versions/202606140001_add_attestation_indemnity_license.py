"""Add attestations, indemnity_policies, indemnity_certificates tables;
   license columns on provenance_records.

Revision ID: 202606140001
Revises: 202606130001
Create Date: 2026-06-14

Part 0 — Attestation Core:
  * attestations: signed attestation statements anchored to the hash chain.

Feature F1 — Indemnity:
  * indemnity_policies: per-workspace eligibility threshold rules.
  * indemnity_certificates: signed underwriting artifacts.

Feature F5 — License-contamination detection:
  * provenance_records: license_status, license_match_license, license_similarity columns.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606140001"
down_revision = "202606130001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Part 0: attestations ─────────────────────────────────────────────────
    op.create_table(
        "attestations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("subject_type", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(256), nullable=False),
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
    )
    op.create_index(
        "ix_attestation_workspace_subject",
        "attestations",
        ["workspace_id", "subject_type", "subject_id"],
    )

    # ── F1: indemnity_policies ───────────────────────────────────────────────
    op.create_table(
        "indemnity_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("rules_json", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_indemnity_policy_workspace"),
    )
    op.create_index("ix_indemnity_policy_workspace", "indemnity_policies", ["workspace_id"])

    # ── F1: indemnity_certificates ───────────────────────────────────────────
    op.create_table(
        "indemnity_certificates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("scope_ref", sa.String(256), nullable=False),
        sa.Column("eligibility", sa.String(16), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("attestation_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_indemnity_cert_workspace", "indemnity_certificates", ["workspace_id"])
    op.create_index(
        "ix_indemnity_cert_attestation_id",
        "indemnity_certificates",
        ["attestation_id"],
    )
    op.create_index(
        "ix_indemnity_cert_workspace_scope",
        "indemnity_certificates",
        ["workspace_id", "scope", "scope_ref"],
    )

    # ── F5: license columns on provenance_records ────────────────────────────
    op.add_column(
        "provenance_records",
        sa.Column("license_status", sa.String(16), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column("license_match_license", sa.String(128), nullable=True),
    )
    op.add_column(
        "provenance_records",
        sa.Column("license_similarity", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_provenance_license_status",
        "provenance_records",
        ["license_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_provenance_license_status", table_name="provenance_records")
    op.drop_column("provenance_records", "license_similarity")
    op.drop_column("provenance_records", "license_match_license")
    op.drop_column("provenance_records", "license_status")

    op.drop_index("ix_indemnity_cert_workspace_scope", table_name="indemnity_certificates")
    op.drop_index("ix_indemnity_cert_attestation_id", table_name="indemnity_certificates")
    op.drop_index("ix_indemnity_cert_workspace", table_name="indemnity_certificates")
    op.drop_table("indemnity_certificates")

    op.drop_index("ix_indemnity_policy_workspace", table_name="indemnity_policies")
    op.drop_table("indemnity_policies")

    op.drop_index("ix_attestation_workspace_subject", table_name="attestations")
    op.drop_table("attestations")
