"""Agent-authority fields on the action ledger (PART 2 #16).

Revision ID: 202606260005
Revises: 202606260004
Create Date: 2026-06-26

The ledger recorded effects but not authority. Add agent_identity,
human_principal, mandate_ref, capability and authority_state (default
"unmandated" — absence of a mandate is not authorisation).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202606260005"
down_revision = "202606260004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "agent_actions" not in tables:
        op.create_table(
            "agent_actions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("workspace_id", sa.String(128), nullable=False),
            sa.Column("session_key", sa.String(64), nullable=False),
            sa.Column("prompt_context_id", sa.String(64), nullable=True),
            sa.Column("action_type", sa.String(32), nullable=False),
            sa.Column("tool_name", sa.String(128), nullable=False),
            sa.Column("arguments_json", sa.JSON(), nullable=True),
            sa.Column("risk_flags_json", sa.JSON(), nullable=True),
            sa.Column("agent_identity", sa.String(256), nullable=True),
            sa.Column("human_principal", sa.String(256), nullable=True),
            sa.Column("mandate_ref", sa.String(256), nullable=True),
            sa.Column("capability", sa.String(64), nullable=True),
            sa.Column("authority_state", sa.String(16), nullable=False, server_default="unmandated"),
            sa.Column("record_hash", sa.String(64), nullable=True),
            sa.Column("prev_hash", sa.String(64), nullable=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_agent_actions_workspace_id", "agent_actions", ["workspace_id"])
        op.create_index("ix_agent_actions_prompt_context_id", "agent_actions", ["prompt_context_id"])
        op.create_index("ix_agent_actions_record_hash", "agent_actions", ["record_hash"])
        op.create_index(
            "ix_agent_action_workspace_session_time",
            "agent_actions",
            ["workspace_id", "session_key", "occurred_at"],
        )
        op.create_index(
            "ix_agent_action_workspace_time",
            "agent_actions",
            ["workspace_id", "occurred_at"],
        )
    else:
        columns = {c["name"] for c in inspector.get_columns("agent_actions")}
        if "agent_identity" not in columns:
            op.add_column("agent_actions", sa.Column("agent_identity", sa.String(256), nullable=True))
        if "human_principal" not in columns:
            op.add_column("agent_actions", sa.Column("human_principal", sa.String(256), nullable=True))
        if "mandate_ref" not in columns:
            op.add_column("agent_actions", sa.Column("mandate_ref", sa.String(256), nullable=True))
        if "capability" not in columns:
            op.add_column("agent_actions", sa.Column("capability", sa.String(64), nullable=True))
        if "authority_state" not in columns:
            op.add_column(
                "agent_actions",
                sa.Column("authority_state", sa.String(16), nullable=False, server_default="unmandated"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_actions" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("agent_actions")}
        if "authority_state" in columns:
            op.drop_column("agent_actions", "authority_state")
        if "capability" in columns:
            op.drop_column("agent_actions", "capability")
        if "mandate_ref" in columns:
            op.drop_column("agent_actions", "mandate_ref")
        if "human_principal" in columns:
            op.drop_column("agent_actions", "human_principal")
        if "agent_identity" in columns:
            op.drop_column("agent_actions", "agent_identity")
