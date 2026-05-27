from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import (
    AuthContext,
    build_record_visibility_clause,
    get_current_auth_context,
    get_verified_user_role,
    require_role,
)
from app.api.routes.request_utils import assert_record_exists as _assert_record_exists
from app.api.routes.request_utils import get_client_ip as _get_client_ip
from app.db.models import ProvenanceRecord, ProvenanceTag
from app.db.session import get_db_session

router = APIRouter(tags=["tags"])
logger = logging.getLogger(__name__)


class AddTagsRequest(BaseModel):
    tags: list[Annotated[str, Field(max_length=128)]] = Field(..., min_length=1, max_length=50)
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


@router.post("/provenance/{record_uuid}/tags", status_code=status.HTTP_201_CREATED)
async def add_tags(
    record_uuid: str,
    payload: AddTagsRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "member", "reviewer"))],
) -> dict:
    """Add tag(s) to a provenance record."""
    if payload.workspace_id and payload.workspace_id != auth.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace mismatch.",
        )

    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )

    await _assert_record_exists(session, record_uuid, auth.workspace_id, [access_clause])

    added: list[str] = []
    for raw_tag in payload.tags:
        tag_val = raw_tag.strip().lower()
        if not tag_val:
            continue

        # Check for duplicate
        existing = await session.execute(
            select(ProvenanceTag).where(
                and_(
                    ProvenanceTag.workspace_id == auth.workspace_id,
                    ProvenanceTag.record_uuid == record_uuid,
                    ProvenanceTag.tag == tag_val,
                )
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue

        new_tag = ProvenanceTag(
            workspace_id=auth.workspace_id,
            record_uuid=record_uuid,
            tag=tag_val,
            created_by=auth.subject,
        )
        session.add(new_tag)
        added.append(tag_val)

    if added:
        await log_audit_event(
            session,
            workspace_id=auth.workspace_id,
            user_id=auth.subject,
            action="tag.add",
            target_uuid=record_uuid,
            details={"tags": added},
            ip_address=_get_client_ip(request),
        )

    await session.commit()

    return {"record_uuid": record_uuid, "added": added}


@router.delete("/provenance/{record_uuid}/tags/{tag}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_tag(
    record_uuid: str,
    tag: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "member", "reviewer"))],
) -> None:
    """Remove a tag from a provenance record."""
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )

    await _assert_record_exists(session, record_uuid, auth.workspace_id, [access_clause])

    tag_val = tag.strip().lower()
    result = await session.execute(
        select(ProvenanceTag).where(
            and_(
                ProvenanceTag.workspace_id == auth.workspace_id,
                ProvenanceTag.record_uuid == record_uuid,
                ProvenanceTag.tag == tag_val,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag '{tag_val}' not found on this record.",
        )

    await session.delete(existing)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="tag.remove",
        target_uuid=record_uuid,
        details={"tag": tag_val},
        ip_address=_get_client_ip(request),
    )

    await session.commit()


@router.get("/provenance/{record_uuid}/tags")
async def list_record_tags(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """List all tags on a provenance record."""
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )

    await _assert_record_exists(session, record_uuid, auth.workspace_id, [access_clause])

    result = await session.execute(
        select(ProvenanceTag)
        .where(
            and_(
                ProvenanceTag.workspace_id == auth.workspace_id,
                ProvenanceTag.record_uuid == record_uuid,
            )
        )
        .order_by(ProvenanceTag.tag)
    )
    tags = result.scalars().all()

    return {
        "record_uuid": record_uuid,
        "tags": [
            {
                "tag": t.tag,
                "created_by": t.created_by,
                "created_at": t.created_at.isoformat(),
            }
            for t in tags
        ],
    }


@router.get("/tags")
async def list_workspace_tags(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """List all unique tags in workspace with counts."""
    result = await session.execute(
        select(ProvenanceTag.tag, func.count(ProvenanceTag.id).label("count"))
        .where(ProvenanceTag.workspace_id == auth.workspace_id)
        .group_by(ProvenanceTag.tag)
        .order_by(desc("count"), ProvenanceTag.tag)
    )
    rows = result.all()

    return {
        "tags": [{"tag": row.tag, "count": row.count} for row in rows],
        "total": len(rows),
    }
