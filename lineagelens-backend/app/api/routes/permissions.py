from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, ensure_workspace_scope, require_role
from app.db.models import ResourcePermission
from app.db.session import get_db_session

router = APIRouter(tags=["permissions"])
logger = logging.getLogger(__name__)

# NOTE: require_role("admin") works both as a decorator argument
# (dependencies=[Depends(require_role("admin"))]) and as a direct FastAPI
# dependency (auth: AuthContext = Depends(require_role("admin"))). Both forms
# are equivalent — require_role returns a closure that FastAPI resolves lazily.


class PermissionGrant(BaseModel):
    record_uuid: str = Field(..., alias="recordUuid")
    user_id: str = Field(..., alias="userId")
    workspace_id: str = Field(..., alias="workspaceId")
    can_view: bool = Field(default=True, alias="canView")
    can_edit: bool = Field(default=False, alias="canEdit")
    can_delete: bool = Field(default=False, alias="canDelete")

    model_config = ConfigDict(populate_by_name=True)


def _ser(p: ResourcePermission) -> dict:
    return {
        "id": p.id,
        "recordUuid": p.record_uuid,
        "userId": p.user_id,
        "workspaceId": p.workspace_id,
        "canView": p.can_view,
        "canEdit": p.can_edit,
        "canDelete": p.can_delete,
        "grantedBy": p.granted_by,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
    }


@router.post("/permissions", status_code=status.HTTP_201_CREATED)
async def grant_permission(
    payload: PermissionGrant,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    ensure_workspace_scope(auth, payload.workspace_id)
    # upsert: update existing if found, else insert
    existing = await session.execute(
        select(ResourcePermission).where(
            and_(
                ResourcePermission.workspace_id == auth.workspace_id,
                ResourcePermission.record_uuid == payload.record_uuid,
                ResourcePermission.user_id == payload.user_id,
            )
        )
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        row.can_view = payload.can_view
        row.can_edit = payload.can_edit
        row.can_delete = payload.can_delete
        row.granted_by = auth.subject
        await session.commit()
        await session.refresh(row)
        return _ser(row)

    perm = ResourcePermission(
        workspace_id=auth.workspace_id,
        record_uuid=payload.record_uuid,
        user_id=payload.user_id,
        can_view=payload.can_view,
        can_edit=payload.can_edit,
        can_delete=payload.can_delete,
        granted_by=auth.subject,
    )
    session.add(perm)
    await session.commit()
    await session.refresh(perm)
    return _ser(perm)


@router.get("/permissions/record/{record_uuid}")
async def get_record_permissions(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    result = await session.execute(
        select(ResourcePermission).where(
            and_(
                ResourcePermission.workspace_id == auth.workspace_id,
                ResourcePermission.record_uuid == record_uuid,
            )
        )
    )
    perms = list(result.scalars().all())
    return {"results": [_ser(p) for p in perms], "count": len(perms)}


@router.delete("/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_permission(
    permission_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> None:
    result = await session.execute(
        select(ResourcePermission).where(
            and_(
                ResourcePermission.id == permission_id,
                ResourcePermission.workspace_id == auth.workspace_id,
            )
        )
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(status_code=404, detail="Permission not found.")
    await session.delete(perm)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="permission.revoke",
        target_uuid=perm.record_uuid,
        details={"permission_id": str(permission_id), "user_id": perm.user_id},
    )
    await session.commit()
