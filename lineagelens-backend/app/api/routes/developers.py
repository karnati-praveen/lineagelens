from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.models import ProvenanceRecord, UserAccount
from app.db.session import get_db_session

router = APIRouter(tags=["developers"])
logger = logging.getLogger(__name__)


class DeveloperActivityRequest(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    date_from: str | None = Field(default=None, alias="dateFrom")
    date_to: str | None = Field(default=None, alias="dateTo")

    model_config = ConfigDict(populate_by_name=True)


@router.post("/analytics/developer-activity")
async def developer_activity(
    payload: DeveloperActivityRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return per-developer AI usage statistics."""
    ensure_workspace_scope(auth, payload.workspace_id)

    from datetime import datetime, timezone
    from sqlalchemy import and_

    filters = [ProvenanceRecord.workspace_id == auth.workspace_id]
    if payload.date_from:
        try:
            filters.append(
                ProvenanceRecord.timestamp_iso
                >= datetime.fromisoformat(payload.date_from).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
    if payload.date_to:
        try:
            filters.append(
                ProvenanceRecord.timestamp_iso
                <= datetime.fromisoformat(payload.date_to).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass

    result = await session.execute(
        select(
            ProvenanceRecord.user_id,
            func.count(ProvenanceRecord.id).label("record_count"),
            func.avg(ProvenanceRecord.risk_score).label("avg_risk"),
            func.max(ProvenanceRecord.timestamp_iso).label("last_active"),
            func.count(func.distinct(ProvenanceRecord.model_name)).label("model_count"),
            func.sum(ProvenanceRecord.token_count).label("total_tokens"),
            func.sum(ProvenanceRecord.cost_usd).label("total_cost_usd"),
        )
        .where(and_(*filters))
        .group_by(ProvenanceRecord.user_id)
        .order_by(func.count(ProvenanceRecord.id).desc())
    )
    rows = result.all()

    # Fetch usernames for user_ids
    user_ids = [str(r.user_id) for r in rows if r.user_id]
    username_map: dict[str, str] = {}
    if user_ids:
        import uuid as uuid_pkg

        parsed = [uuid_pkg.UUID(uid) for uid in user_ids if uid]
        ua_result = await session.execute(
            select(UserAccount.id, UserAccount.username).where(UserAccount.id.in_(parsed))
        )
        username_map = {str(ua.id): ua.username for ua in ua_result.all()}

    developers = []
    for row in rows:
        uid = str(row.user_id) if row.user_id else None
        developers.append({
            "userId": uid,
            "username": username_map.get(uid, uid) if uid else "unknown",
            "recordCount": row.record_count,
            "avgRisk": round(float(row.avg_risk or 0), 1),
            "lastActive": row.last_active.isoformat() if row.last_active else None,
            "modelCount": row.model_count,
            "totalTokens": row.total_tokens or 0,
            "totalCostUsd": round(float(row.total_cost_usd or 0), 4),
        })

    return {"results": developers, "count": len(developers)}
