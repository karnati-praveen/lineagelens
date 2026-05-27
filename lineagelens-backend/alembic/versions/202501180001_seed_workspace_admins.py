"""Promote one user per workspace to admin when no admin exists.

Revision ID: 202501180001
Revises: 202501170001
Create Date: 2025-01-18 00:01:00
"""

from __future__ import annotations

from alembic import op


revision = "202501180001"
down_revision = "202501170001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked_users AS (
            SELECT
                id,
                workspace_id,
                ROW_NUMBER() OVER (
                    PARTITION BY workspace_id
                    ORDER BY CASE WHEN is_active THEN 0 ELSE 1 END,
                             created_at ASC,
                             id ASC
                ) AS rn
            FROM user_accounts
        ),
        workspaces_without_admin AS (
            SELECT workspace_id
            FROM user_accounts
            GROUP BY workspace_id
            HAVING COUNT(*) FILTER (WHERE role = 'admin') = 0
        )
        UPDATE user_accounts AS ua
        SET role = 'admin'
        FROM ranked_users AS ru
        JOIN workspaces_without_admin AS wa
          ON wa.workspace_id = ru.workspace_id
        WHERE ua.id = ru.id
          AND ru.rn = 1
        """
    )


def downgrade() -> None:
    """No-op: reversing a workspace admin seed is unsafe.

    Demoting users who were promoted by this migration cannot be done safely
    in a generic, non-destructive way because we cannot reliably distinguish
    accounts promoted here from those promoted by other means.  Any rollback
    should be handled manually on a case-by-case basis.
    """