"""Add composite indexes for common query patterns

Revision ID: 202501240001
Revises: 202501230001
Create Date: 2026-05-17

"""
from __future__ import annotations

from alembic import op

revision = "202501240001"
down_revision = "202501230001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_saved_query_workspace_user", "saved_queries", ["workspace_id", "user_id"])
    op.create_index("ix_saved_query_workspace_created", "saved_queries", ["workspace_id", "created_at"])
    op.create_index("ix_tag_workspace_created", "provenance_tags", ["workspace_id", "created_at"])
    op.create_index("ix_comment_record_created", "provenance_comments", ["workspace_id", "record_uuid", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_comment_record_created", table_name="provenance_comments")
    op.drop_index("ix_tag_workspace_created", table_name="provenance_tags")
    op.drop_index("ix_saved_query_workspace_created", table_name="saved_queries")
    op.drop_index("ix_saved_query_workspace_user", table_name="saved_queries")
