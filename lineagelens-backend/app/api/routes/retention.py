from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_current_auth_context, require_admin
from app.db.session import get_db_session, get_session_factory
from app.services.retention_service import (
    get_retention_policy,
    run_retention_cleanup,
    upsert_retention_policy,
)

router = APIRouter(tags=["retention"])
logger = logging.getLogger(__name__)


class RetentionPolicyUpdate(BaseModel):
    retain_days: int = Field(..., ge=1, le=3650)
    redact_after_days: int | None = Field(default=None, ge=1, le=3650)
    enabled: bool = False

    model_config = ConfigDict(populate_by_name=True)


@router.get("/retention")
async def get_retention(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Get the workspace retention policy."""
    policy = await get_retention_policy(session, auth.workspace_id)
    if policy is None:
        return {
            "workspace_id": auth.workspace_id,
            "retain_days": 365,
            "redact_after_days": None,
            "enabled": False,
            "created_at": None,
            "updated_at": None,
        }
    return {
        "workspace_id": policy.workspace_id,
        "retain_days": policy.retain_days,
        "redact_after_days": policy.redact_after_days,
        "enabled": policy.enabled,
        "created_at": policy.created_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
    }


@router.put("/retention")
async def update_retention(
    payload: RetentionPolicyUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Update the workspace retention policy. Admin only."""
    policy = await upsert_retention_policy(
        session=session,
        workspace_id=auth.workspace_id,
        retain_days=payload.retain_days,
        redact_after_days=payload.redact_after_days,
        enabled=payload.enabled,
    )

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="retention.update",
        details={
            "retain_days": payload.retain_days,
            "redact_after_days": payload.redact_after_days,
            "enabled": payload.enabled,
        },
    )

    await session.commit()
    await session.refresh(policy)

    return {
        "workspace_id": policy.workspace_id,
        "retain_days": policy.retain_days,
        "redact_after_days": policy.redact_after_days,
        "enabled": policy.enabled,
        "created_at": policy.created_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
    }


@router.post("/retention/run")
async def run_retention(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Manually trigger retention cleanup for this workspace. Admin only."""
    session_factory = get_session_factory(request)

    result = await run_retention_cleanup(
        session_factory=session_factory,
        workspace_id=auth.workspace_id,
    )

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="retention.update",
        details={"manual_run": True, **result},
    )

    await session.commit()
    return result
