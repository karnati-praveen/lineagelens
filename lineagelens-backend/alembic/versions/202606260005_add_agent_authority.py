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
    op.add_column("agent_actions", sa.Column("agent_identity", sa.String(256), nullable=True))
    op.add_column("agent_actions", sa.Column("human_principal", sa.String(256), nullable=True))
    op.add_column("agent_actions", sa.Column("mandate_ref", sa.String(256), nullable=True))
    op.add_column("agent_actions", sa.Column("capability", sa.String(64), nullable=True))
    op.add_column(
        "agent_actions",
        sa.Column("authority_state", sa.String(16), nullable=False, server_default="unmandated"),
    )


def downgrade() -> None:
    op.drop_column("agent_actions", "authority_state")
    op.drop_column("agent_actions", "capability")
    op.drop_column("agent_actions", "mandate_ref")
    op.drop_column("agent_actions", "human_principal")
    op.drop_column("agent_actions", "agent_identity")
