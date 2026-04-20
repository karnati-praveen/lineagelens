from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import normalize_username, validate_password_strength
from app.core.config import Settings, get_settings
from app.core.security import AuthContext, get_current_auth_context, hash_password
from app.db.models import ProvenanceRecord, UserAccount
from app.db.session import get_db_session
from app.schemas.team import InviteMemberRequest, InviteMemberResponse, TeamMembersResponse
from app.services.team_service import build_team_member_stats


router = APIRouter(prefix="/team", tags=["team"])


@router.get("/members", response_model=TeamMembersResponse)
async def list_team_members(
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
) -> TeamMembersResponse:
    users_stmt = (
        select(UserAccount)
        .where(UserAccount.workspace_id == auth.workspace_id, UserAccount.is_active.is_(True))
        .order_by(UserAccount.created_at)
    )
    users_result = await session.execute(users_stmt)
    users = users_result.scalars().all()

    counts_stmt = (
        select(ProvenanceRecord.user_id, func.count(ProvenanceRecord.id).label("cnt"))
        .where(ProvenanceRecord.workspace_id == auth.workspace_id)
        .group_by(ProvenanceRecord.user_id)
    )
    counts_result = await session.execute(counts_stmt)
    record_counts: dict[str, int] = {
        str(row.user_id): row.cnt for row in counts_result if row.user_id is not None
    }

    members = build_team_member_stats(users, record_counts)

    return TeamMembersResponse(workspaceId=auth.workspace_id, members=members)


@router.post("/invite", response_model=InviteMemberResponse, status_code=status.HTTP_201_CREATED)
async def invite_team_member(
    payload: InviteMemberRequest,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
    settings: Settings = Depends(get_settings),
) -> InviteMemberResponse:
    caller_stmt = select(UserAccount).where(UserAccount.id == _parse_uuid(auth.subject))
    caller_result = await session.execute(caller_stmt)
    caller = caller_result.scalar_one_or_none()

    if caller is None or not caller.is_active or caller.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can invite team members.",
        )

    username = normalize_username(payload.username)
    validate_password_strength(payload.password, settings)

    existing = await session.execute(select(UserAccount).where(UserAccount.username == username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered.",
        )

    new_user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        workspace_id=auth.workspace_id,
        role=payload.role,
        is_active=True,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    return InviteMemberResponse(
        id=str(new_user.id),
        username=new_user.username,
        workspaceId=new_user.workspace_id,
        role=new_user.role,
    )


def _parse_uuid(value: str):
    import uuid
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None
