from __future__ import annotations

import logging

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
    except Exception as exc:
        logger.warning("Audit log write failed: %s", exc)
