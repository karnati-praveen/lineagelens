from __future__ import annotations

import logging
import uuid as uuid_pkg
from typing import Annotated
from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_current_auth_context, require_admin
from app.db.models import UserAccount, Workspace
from app.db.session import get_db_session


router = APIRouter(prefix="/workspaces", tags=["workspaces"])
logger = logging.getLogger(__name__)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    workspace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("workspace_id", "workspaceId", "id"),
    )

    model_config = ConfigDict(populate_by_name=True)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    settings: dict | None = None

    model_config = ConfigDict(populate_by_name=True)


def _ser_workspace(ws: Workspace) -> dict:
    return {
        "id": ws.id,
        "workspaceId": ws.id,
        "name": ws.name,
        "ownerId": ws.owner_id,
        "settings": ws.settings,
        "createdAt": ws.created_at.isoformat() if ws.created_at else None,
        "updatedAt": ws.updated_at.isoformat() if ws.updated_at else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Create a new isolated workspace. The calling user is recorded as owner."""
    new_id = payload.workspace_id or str(uuid_pkg.uuid4())

    existing = await session.execute(
        select(Workspace).where(Workspace.id == new_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Workspace ID already exists.")

    ws = Workspace(
        id=new_id,
        name=payload.name,
        owner_id=auth.subject,
        settings={},
    )
    session.add(ws)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="workspace.create",
        details={"new_workspace_id": new_id, "name": payload.name},
    )
    await session.commit()
    await session.refresh(ws)
    return _ser_workspace(ws)


@router.get("")
async def get_current_workspace(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return the caller's current workspace."""
    result = await session.execute(
        select(Workspace).where(Workspace.id == auth.workspace_id)
    )
    ws = result.scalar_one_or_none()
    if ws is not None:
        return _ser_workspace(ws)
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: Annotated[str, Path()],
    payload: WorkspaceUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Update workspace name or settings (admin of that workspace only)."""
    if workspace_id != auth.workspace_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot modify a different workspace.")

    result = await session.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    ws = result.scalar_one_or_none()
    if ws is None:
        ws = Workspace(
            id=workspace_id,
            name=payload.name or workspace_id,
            owner_id=auth.subject,
            settings=payload.settings or {},
        )
        session.add(ws)
    else:
        if payload.name is not None:
            ws.name = payload.name
        if payload.settings is not None:
            ws.settings = {**(ws.settings or {}), **payload.settings}

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="workspace.update",
        details={"name": payload.name, "settings_keys": list((payload.settings or {}).keys())},
    )
    await session.commit()
    await session.refresh(ws)
    return _ser_workspace(ws)


@router.get("/me")
async def get_my_workspace(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return current user's workspace info including active member count."""
    try:
        user_id = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
        )

    user_result = await session.execute(
        select(UserAccount.username, UserAccount.role, UserAccount.created_at, UserAccount.is_active)
        .where(UserAccount.id == user_id)
    )
    user_row = user_result.one_or_none()
    if user_row is None or not user_row.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found.",
        )

    count_result = await session.execute(
        select(func.count(UserAccount.id)).where(
            UserAccount.workspace_id == auth.workspace_id,
            UserAccount.is_active.is_(True),
        )
    )
    member_count = count_result.scalar_one_or_none() or 0

    return {
        "workspaceId": auth.workspace_id,
        "username": user_row.username,
        "role": user_row.role,
        "memberCount": member_count,
        "accountCreatedAt": user_row.created_at.isoformat() if user_row.created_at else None,
    }


@router.get("/{workspace_id}/members")
async def list_workspace_members(
    workspace_id: Annotated[str, Path()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """List active users in the same workspace (admin only)."""
    if workspace_id != auth.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace mismatch: cannot access a different workspace.",
        )

    members_result = await session.execute(
        select(
            UserAccount.id,
            UserAccount.username,
            UserAccount.role,
            UserAccount.is_active,
            UserAccount.created_at,
        )
        .where(
            UserAccount.workspace_id == workspace_id,
            UserAccount.is_active.is_(True),
        )
        .order_by(UserAccount.created_at)
    )
    members = members_result.all()

    return {
        "workspaceId": workspace_id,
        "memberCount": len(members),
        "members": [
            {
                "id": str(row.id),
                "username": row.username,
                "role": row.role,
                "isActive": row.is_active,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
            }
            for row in members
        ],
    }
