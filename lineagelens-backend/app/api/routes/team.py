from __future__ import annotations

import uuid as uuid_pkg
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import normalize_username, validate_password_strength
from app.core.audit import log_audit_event
from app.core.config import Settings, get_settings
from app.core.mode_guard import require_non_solo
from app.core.security import (
    AuthContext,
    build_record_visibility_clause,
    get_current_auth_context,
    get_verified_user_role,
    hash_password,
    require_admin,
)
from app.db.models import ProvenanceRecord, UserAccount
from app.db.session import get_db_session
from app.schemas.team import InviteMemberRequest, InviteMemberResponse, TeamMembersResponse
from app.services.team_service import build_team_member_stats

VALID_ROLES = {"admin", "member", "viewer", "reviewer", "auditor", "data-engineer"}

router = APIRouter(prefix="/team", tags=["team"])

_MEMBER_NOT_FOUND = "Member not found."


class UpdateRoleRequest(BaseModel):
    role: str = Field(..., min_length=1, max_length=32)

    model_config = ConfigDict(populate_by_name=True)


@router.get("/members", dependencies=[Depends(require_non_solo)])
async def list_team_members(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> TeamMembersResponse:
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )

    users_stmt = (
        select(UserAccount)
        .where(UserAccount.workspace_id == auth.workspace_id, UserAccount.is_active.is_(True))
        .order_by(UserAccount.created_at)
    )
    users_result = await session.execute(users_stmt)
    users = users_result.scalars().all()

    counts_stmt = (
        select(ProvenanceRecord.user_id, func.count(ProvenanceRecord.id).label("cnt"))
        .where(ProvenanceRecord.workspace_id == auth.workspace_id, access_clause)
        .group_by(ProvenanceRecord.user_id)
    )
    counts_result = await session.execute(counts_stmt)
    record_counts: dict[str, int] = {
        str(row.user_id): row.cnt for row in counts_result if row.user_id is not None
    }

    members = build_team_member_stats(users, record_counts)

    return TeamMembersResponse(workspaceId=auth.workspace_id, members=members)


@router.post("/invite", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_non_solo)])
async def invite_team_member(
    payload: InviteMemberRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InviteMemberResponse:
    caller_id = _parse_uuid(auth.subject)
    if caller_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication subject.",
        )

    caller_stmt = select(UserAccount).where(
        UserAccount.id == caller_id,
        UserAccount.workspace_id == auth.workspace_id,
    )
    caller_result = await session.execute(caller_stmt)
    caller = caller_result.scalar_one_or_none()

    if caller is None or not caller.is_active or caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can invite team members.",
        )

    username = normalize_username(payload.username)
    validate_password_strength(payload.password, settings)

    existing = await session.execute(
        select(UserAccount).where(
            UserAccount.username == username,
            UserAccount.workspace_id == auth.workspace_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered.",
        )

    if payload.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{payload.role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}.",
        )

    new_user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        workspace_id=auth.workspace_id,
        role=payload.role,
        is_active=True,
    )
    session.add(new_user)
    try:
        await session.commit()
        await session.refresh(new_user)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered.",
        )

    return InviteMemberResponse(
        id=str(new_user.id),
        username=new_user.username,
        workspaceId=new_user.workspace_id,
        role=new_user.role,
    )


@router.get("/members/{user_id}", dependencies=[Depends(require_non_solo)])
async def get_team_member(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Get a single team member by ID (admin only)."""
    parsed = _parse_uuid(user_id)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_MEMBER_NOT_FOUND)

    result = await session.execute(
        select(UserAccount).where(
            UserAccount.id == parsed,
            UserAccount.workspace_id == auth.workspace_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_MEMBER_NOT_FOUND)

    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "isActive": user.is_active,
        "workspaceId": user.workspace_id,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
    }


@router.patch("/members/{user_id}/role", dependencies=[Depends(require_non_solo)])
async def update_member_role(
    user_id: str,
    payload: UpdateRoleRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Change a team member's role (admin only). Valid roles: admin, member, viewer, reviewer, auditor, data-engineer."""
    if payload.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{payload.role}'. Valid roles: {', '.join(sorted(VALID_ROLES))}.",
        )

    parsed = _parse_uuid(user_id)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_MEMBER_NOT_FOUND)

    result = await session.execute(
        select(UserAccount).where(
            UserAccount.id == parsed,
            UserAccount.workspace_id == auth.workspace_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_MEMBER_NOT_FOUND)

    if str(user.id) == auth.subject and payload.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot demote themselves.",
        )

    old_role = user.role
    user.role = payload.role

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="team.role_update",
        target_uuid=user_id,
        details={"old_role": old_role, "new_role": payload.role, "target_user": user.username},
    )

    await session.commit()
    await session.refresh(user)

    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "workspaceId": user.workspace_id,
    }


@router.patch("/members/{user_id}/deactivate", dependencies=[Depends(require_non_solo)])
async def deactivate_member(
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Deactivate a team member (admin only). Revokes all active tokens."""
    parsed = _parse_uuid(user_id)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_MEMBER_NOT_FOUND)

    if uuid_pkg.UUID(user_id) == uuid_pkg.UUID(str(auth.subject)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account.")

    result = await session.execute(
        select(UserAccount).where(
            UserAccount.id == parsed,
            UserAccount.workspace_id == auth.workspace_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_MEMBER_NOT_FOUND)

    user.is_active = False
    user.token_version = (user.token_version or 0) + 1  # revoke all tokens

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="team.deactivate",
        target_uuid=user_id,
        details={"target_user": user.username},
    )

    await session.commit()
    return {"id": str(user.id), "username": user.username, "isActive": False}


def _parse_uuid(value: str) -> uuid_pkg.UUID | None:
    try:
        return uuid_pkg.UUID(value)
    except (ValueError, TypeError):
        return None
