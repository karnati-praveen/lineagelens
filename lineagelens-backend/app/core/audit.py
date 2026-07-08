from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog

logger = logging.getLogger(__name__)


async def log_audit_event(
    session: AsyncSession,
    *,
    workspace_id: str,
    user_id: str | None,
    action: str,
    target_uuid: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
) -> None:
    """Fire-and-forget audit log write. Never raises."""
    try:
        entry = AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_uuid=target_uuid,
            details=details,
            ip_address=ip_address,
        )
        session.add(entry)
        await session.flush()
    except Exception:
        logger.exception("Audit log write failed")


async def list_audit_events(
    session: AsyncSession,
    workspace_id: str,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict]:
    """All audit-log rows for a workspace, optionally date-bounded.

    Used by the evidence capsule (PART 5 #51) to bundle the audit trail
    alongside the records/policies/lifecycle events it relates to.
    """
    filters = [AuditLog.workspace_id == workspace_id]
    if date_from is not None:
        filters.append(AuditLog.created_at >= date_from)
    if date_to is not None:
        filters.append(AuditLog.created_at <= date_to)
    result = await session.execute(
        select(AuditLog).where(*filters).order_by(AuditLog.created_at.asc())
    )
    return [
        {
            "id": row.id,
            "userId": row.user_id,
            "action": row.action,
            "targetUuid": row.target_uuid,
            "details": row.details,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }
        for row in result.scalars().all()
    ]
