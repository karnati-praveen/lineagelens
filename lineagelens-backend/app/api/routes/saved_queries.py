from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_current_auth_context
from app.db.models import SavedQuery
from app.db.session import get_db_session

router = APIRouter(tags=["saved-queries"])
logger = logging.getLogger(__name__)


class SavedQueryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    query: dict
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


@router.post("/saved-queries", status_code=status.HTTP_201_CREATED)
async def create_saved_query(
    payload: SavedQueryCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Save a search query filter set for later reuse."""
    if payload.workspace_id and payload.workspace_id != auth.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace mismatch: cannot save query for a different workspace.",
        )

    saved = SavedQuery(
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        name=payload.name,
        query=payload.query,
    )
    session.add(saved)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="saved_query.create",
        details={"name": payload.name},
    )

    await session.commit()
    await session.refresh(saved)

    return {
        "id": str(saved.id),
        "workspace_id": saved.workspace_id,
        "user_id": saved.user_id,
        "name": saved.name,
        "query": saved.query,
        "created_at": saved.created_at.isoformat(),
    }


@router.get("/saved-queries")
async def list_saved_queries(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """List all saved queries for the workspace."""
    result = await session.execute(
        select(SavedQuery)
        .where(SavedQuery.workspace_id == auth.workspace_id)
        .order_by(SavedQuery.created_at.desc())
    )
    queries = result.scalars().all()

    return {
        "results": [
            {
                "id": str(q.id),
                "workspace_id": q.workspace_id,
                "user_id": q.user_id,
                "name": q.name,
                "query": q.query,
                "created_at": q.created_at.isoformat(),
            }
            for q in queries
        ],
        "total": len(queries),
    }


@router.delete("/saved-queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_saved_query(
    query_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> None:
    """Delete a saved query. Users can only delete their own queries unless admin."""
    import uuid as uuid_pkg

    try:
        parsed_id = uuid_pkg.UUID(query_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved query not found.",
        )

    result = await session.execute(
        select(SavedQuery).where(
            and_(
                SavedQuery.id == parsed_id,
                SavedQuery.workspace_id == auth.workspace_id,
            )
        )
    )
    saved = result.scalar_one_or_none()
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved query not found.",
        )

    # Only the owner or an admin may delete
    from app.db.models import UserAccount
    from sqlalchemy import select as sa_select
    from uuid import UUID as PyUUID

    try:
        user_uuid = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject.")

    role_result = await session.execute(
        sa_select(UserAccount.role).where(UserAccount.id == user_uuid)
    )
    current_role = role_result.scalar_one_or_none() or "member"

    if saved.user_id != auth.subject and current_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own saved queries.",
        )

    session.delete(saved)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="saved_query.delete",
        details={"name": saved.name, "query_id": query_id},
    )

    await session.commit()
