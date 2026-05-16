from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_current_auth_context, require_role
from app.db.models import ProvenanceComment, ProvenanceRecord
from app.db.session import get_db_session

router = APIRouter(tags=["comments"])
logger = logging.getLogger(__name__)


class AddCommentRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=10000)
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


def _get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def _assert_record_exists(session: AsyncSession, record_uuid: str, workspace_id: str) -> None:
    import uuid as uuid_pkg

    try:
        parsed_uuid = uuid_pkg.UUID(record_uuid)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )

    result = await session.execute(
        select(ProvenanceRecord.uuid).where(
            and_(
                ProvenanceRecord.uuid == parsed_uuid,
                ProvenanceRecord.workspace_id == workspace_id,
            )
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )


@router.post("/provenance/{record_uuid}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    record_uuid: str,
    payload: AddCommentRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "member", "reviewer"))],
) -> dict:
    """Add a comment to a provenance record."""
    if payload.workspace_id and payload.workspace_id != auth.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace mismatch.",
        )

    await _assert_record_exists(session, record_uuid, auth.workspace_id)

    comment = ProvenanceComment(
        workspace_id=auth.workspace_id,
        record_uuid=record_uuid,
        user_id=auth.subject,
        body=payload.body.strip(),
    )
    session.add(comment)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="comment.add",
        target_uuid=record_uuid,
        details={"body_length": len(payload.body)},
        ip_address=_get_client_ip(request),
    )

    await session.commit()
    await session.refresh(comment)

    return {
        "id": str(comment.id),
        "record_uuid": comment.record_uuid,
        "user_id": comment.user_id,
        "body": comment.body,
        "created_at": comment.created_at.isoformat(),
        "updated_at": comment.updated_at.isoformat(),
    }


@router.get("/provenance/{record_uuid}/comments")
async def list_comments(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """List comments on a provenance record."""
    await _assert_record_exists(session, record_uuid, auth.workspace_id)

    result = await session.execute(
        select(ProvenanceComment)
        .where(
            and_(
                ProvenanceComment.workspace_id == auth.workspace_id,
                ProvenanceComment.record_uuid == record_uuid,
            )
        )
        .order_by(ProvenanceComment.created_at)
    )
    comments = result.scalars().all()

    return {
        "record_uuid": record_uuid,
        "comments": [
            {
                "id": str(c.id),
                "user_id": c.user_id,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in comments
        ],
        "total": len(comments),
    }


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_comment(
    comment_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> None:
    """Delete a comment. Users can delete their own comments; admins can delete any."""
    import uuid as uuid_pkg
    from uuid import UUID as PyUUID
    from app.db.models import UserAccount
    from sqlalchemy import select as sa_select

    try:
        parsed_id = uuid_pkg.UUID(comment_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found.",
        )

    result = await session.execute(
        select(ProvenanceComment).where(
            and_(
                ProvenanceComment.id == parsed_id,
                ProvenanceComment.workspace_id == auth.workspace_id,
            )
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found.",
        )

    # Check if user is owner or admin
    try:
        user_uuid = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject.")

    role_result = await session.execute(
        sa_select(UserAccount.role).where(UserAccount.id == user_uuid)
    )
    current_role = role_result.scalar_one_or_none() or "member"

    if comment.user_id != auth.subject and current_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments.",
        )

    record_uuid = comment.record_uuid
    await session.delete(comment)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="comment.delete",
        target_uuid=record_uuid,
        details={"comment_id": comment_id},
        ip_address=_get_client_ip(request),
    )

    await session.commit()
