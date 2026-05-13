"""Initial database schema for provenance backend.

Revision ID: 202501150001
Revises:
Create Date: 2025-01-15 00:01:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "202501150001"
down_revision = None
branch_labels = None
depends_on = None

_NOW_SQL = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "provenance_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("request_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_uri", sa.Text(), nullable=True),
        sa.Column("cursor_line", sa.Integer(), nullable=True),
        sa.Column("cursor_column", sa.Integer(), nullable=True),
        sa.Column("timestamp_iso", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_messages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_name", sa.String(length=256), nullable=True),
        sa.Column("model_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_model_response", sa.Text(), nullable=True),
        sa.Column("inserted_code", sa.Text(), nullable=False),
        sa.Column("surrounding_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("embeddings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ast_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("embedding_vector", Vector(dim=256), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("lineage_node_id", sa.String(length=128), nullable=True),
        sa.Column("provenance_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_provenance_records_model_name", "provenance_records", ["model_name"], unique=False)
    op.create_index("ix_provenance_records_timestamp_iso", "provenance_records", ["timestamp_iso"], unique=False)
    op.create_index("ix_provenance_records_uuid", "provenance_records", ["uuid"], unique=True)
    op.create_index("ix_provenance_records_workspace_id", "provenance_records", ["workspace_id"], unique=False)
    op.create_index(
        "ix_provenance_workspace_model",
        "provenance_records",
        ["workspace_id", "model_name"],
        unique=False,
    )
    op.create_index(
        "ix_provenance_workspace_timestamp",
        "provenance_records",
        ["workspace_id", "timestamp_iso"],
        unique=False,
    )

    op.create_table(
        "user_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW_SQL, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_user_accounts_username", "user_accounts", ["username"], unique=True)
    op.create_index("ix_user_accounts_workspace_id", "user_accounts", ["workspace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_accounts_workspace_id", table_name="user_accounts")
    op.drop_index("ix_user_accounts_username", table_name="user_accounts")
    op.drop_table("user_accounts")

    op.drop_index("ix_provenance_workspace_timestamp", table_name="provenance_records")
    op.drop_index("ix_provenance_workspace_model", table_name="provenance_records")
    op.drop_index("ix_provenance_records_workspace_id", table_name="provenance_records")
    op.drop_index("ix_provenance_records_uuid", table_name="provenance_records")
    op.drop_index("ix_provenance_records_timestamp_iso", table_name="provenance_records")
    op.drop_index("ix_provenance_records_model_name", table_name="provenance_records")
    op.drop_table("provenance_records")
