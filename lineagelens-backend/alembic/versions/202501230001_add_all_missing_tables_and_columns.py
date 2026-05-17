"""Add all missing tables and provenance_records columns.

Tables added: audit_logs, saved_queries, provenance_tags, provenance_comments,
retention_policies, policies, resource_permissions, review_queue, alert_configs,
api_keys, github_integrations, scheduled_reports, oidc_providers.

Columns added to provenance_records: risk_score, token_count, cost_usd, is_redacted.
Index added: ix_provenance_workspace_risk (workspace_id, risk_score).

Revision ID: 202501230001
Revises: 202501220001
Create Date: 2026-05-17 00:00:02.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202501230001"
down_revision: str | None = "202501220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW_SQL = sa.text("now()")


def upgrade() -> None:
    # ── provenance_records: add missing columns ────────────────────────────────
    op.add_column("provenance_records", sa.Column("risk_score", sa.Integer(), nullable=True))
    op.add_column("provenance_records", sa.Column("token_count", sa.Integer(), nullable=True))
    op.add_column("provenance_records", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column(
        "provenance_records",
        sa.Column("is_redacted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_provenance_records_risk_score",
        "provenance_records",
        ["risk_score"],
        unique=False,
    )
    op.create_index(
        "ix_provenance_workspace_risk",
        "provenance_records",
        ["workspace_id", "risk_score"],
        unique=False,
    )

    # ── audit_logs ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_uuid", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_workspace_id", "audit_logs", ["workspace_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # ── saved_queries ──────────────────────────────────────────────────────────
    op.create_table(
        "saved_queries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_saved_queries_workspace_id", "saved_queries", ["workspace_id"])

    # ── provenance_tags ────────────────────────────────────────────────────────
    op.create_table(
        "provenance_tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("record_uuid", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provenance_tags_workspace_id", "provenance_tags", ["workspace_id"])
    op.create_index("ix_provenance_tags_record_uuid", "provenance_tags", ["record_uuid"])
    op.create_index("ix_tag_record_tag", "provenance_tags", ["record_uuid", "tag"])

    # ── provenance_comments ────────────────────────────────────────────────────
    op.create_table(
        "provenance_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("record_uuid", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provenance_comments_workspace_id", "provenance_comments", ["workspace_id"])
    op.create_index("ix_provenance_comments_record_uuid", "provenance_comments", ["record_uuid"])

    # ── retention_policies ─────────────────────────────────────────────────────
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("retain_days", sa.Integer(), nullable=False),
        sa.Column("redact_after_days", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_retention_policies_workspace_id"),
    )

    # ── policies ───────────────────────────────────────────────────────────────
    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("policy_type", sa.String(length=64), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policies_workspace_id", "policies", ["workspace_id"])

    # ── resource_permissions ───────────────────────────────────────────────────
    op.create_table(
        "resource_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("record_uuid", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("can_view", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("can_edit", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("can_delete", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("granted_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resource_permissions_workspace_id", "resource_permissions", ["workspace_id"])
    op.create_index("ix_resource_permissions_record_uuid", "resource_permissions", ["record_uuid"])
    op.create_index("ix_resource_perm_record_user", "resource_permissions", ["record_uuid", "user_id"])

    # ── review_queue ───────────────────────────────────────────────────────────
    op.create_table(
        "review_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("record_uuid", sa.String(length=64), nullable=False),
        sa.Column("assigned_to", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_queue_workspace_id", "review_queue", ["workspace_id"])
    op.create_index("ix_review_queue_record_uuid", "review_queue", ["record_uuid"])

    # ── alert_configs ──────────────────────────────────────────────────────────
    op.create_table(
        "alert_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trigger_on", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_configs_workspace_id", "alert_configs", ["workspace_id"])

    # ── api_keys ───────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    # ── github_integrations ────────────────────────────────────────────────────
    op.create_table(
        "github_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("token", sa.String(length=512), nullable=True),
        sa.Column("webhook_secret", sa.String(length=256), nullable=True),
        sa.Column("risk_threshold", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("block_on_high_risk", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "allowed_repos",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_github_integrations_workspace_id"),
    )
    op.create_index("ix_github_integrations_workspace_id", "github_integrations", ["workspace_id"])

    # ── scheduled_reports ─────────────────────────────────────────────────────
    op.create_table(
        "scheduled_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("report_type", sa.String(length=64), nullable=False),
        sa.Column("frequency", sa.String(length=32), nullable=False, server_default="weekly"),
        sa.Column(
            "recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_reports_workspace_id", "scheduled_reports", ["workspace_id"])

    # ── oidc_providers ─────────────────────────────────────────────────────────
    op.create_table(
        "oidc_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("client_id", sa.String(length=256), nullable=False),
        sa.Column("client_secret", sa.String(length=512), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("default_role", sa.String(length=32), nullable=False, server_default="member"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oidc_providers_workspace_id", "oidc_providers", ["workspace_id"])


def downgrade() -> None:
    op.drop_table("oidc_providers")
    op.drop_table("scheduled_reports")
    op.drop_table("github_integrations")
    op.drop_table("api_keys")
    op.drop_table("alert_configs")
    op.drop_table("review_queue")
    op.drop_table("resource_permissions")
    op.drop_table("policies")
    op.drop_table("retention_policies")
    op.drop_table("provenance_comments")
    op.drop_table("provenance_tags")
    op.drop_table("saved_queries")
    op.drop_table("audit_logs")

    op.drop_index("ix_provenance_workspace_risk", table_name="provenance_records")
    op.drop_index("ix_provenance_records_risk_score", table_name="provenance_records")
    op.drop_column("provenance_records", "is_redacted")
    op.drop_column("provenance_records", "cost_usd")
    op.drop_column("provenance_records", "token_count")
    op.drop_column("provenance_records", "risk_score")
