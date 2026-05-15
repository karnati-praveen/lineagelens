from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, require_admin
from app.db.models import AuditLog
from app.db.session import get_db_session

router = APIRouter(tags=["audit"])
logger = logging.getLogger(__name__)


@router.get("/audit-log")
async def list_audit_log(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
    action: Annotated[str | None, Query()] = None,
    target_uuid: Annotated[str | None, Query(alias="targetUuid")] = None,
    date_from: Annotated[str | None, Query(alias="from")] = None,
    date_to: Annotated[str | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """List audit log entries for the workspace. Admin only."""
    from datetime import datetime

    filters = [AuditLog.workspace_id == auth.workspace_id]

    if action:
        filters.append(AuditLog.action == action)

    if target_uuid:
        filters.append(AuditLog.target_uuid == target_uuid)

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            filters.append(AuditLog.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
            filters.append(AuditLog.created_at <= dt_to)
        except ValueError:
            pass

    from sqlalchemy import func

    count_result = await session.execute(
        select(func.count()).select_from(AuditLog).where(and_(*filters))
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(AuditLog)
        .where(and_(*filters))
        .order_by(desc(AuditLog.created_at))
        .offset(offset)
        .limit(limit)
    )
    entries = result.scalars().all()

    return {
        "results": [
            {
                "id": entry.id,
                "workspace_id": entry.workspace_id,
                "user_id": entry.user_id,
                "action": entry.action,
                "target_uuid": entry.target_uuid,
                "details": entry.details,
                "ip_address": entry.ip_address,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in entries
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": (offset + limit) < total,
    }
