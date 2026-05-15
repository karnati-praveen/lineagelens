from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, ensure_workspace_scope, require_role
from app.db.models import ReviewQueue
from app.db.session import get_db_session

router = APIRouter(tags=["reviews"])
logger = logging.getLogger(__name__)

VALID_STATUSES = {"pending", "approved", "rejected", "deferred"}


class ReviewCreate(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    record_uuid: str = Field(..., alias="recordUuid")
    assigned_to: str | None = Field(default=None, alias="assignedTo")
    notes: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class ReviewUpdate(BaseModel):
    status: str | None = None
    notes: str | None = None
    assigned_to: str | None = Field(default=None, alias="assignedTo")

    model_config = ConfigDict(populate_by_name=True)


def _ser(r: ReviewQueue) -> dict:
    return {
        "id": str(r.id),
        "workspaceId": r.workspace_id,
        "recordUuid": r.record_uuid,
        "assignedTo": r.assigned_to,
        "status": r.status,
        "notes": r.notes,
        "reviewedBy": r.reviewed_by,
        "createdBy": r.created_by,
        "createdAt": r.created_at.isoformat() if r.created_at else None,
        "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
async def create_review(
    payload: ReviewCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "reviewer"))],
) -> dict:
    ensure_workspace_scope(auth, payload.workspace_id)
    item = ReviewQueue(
        workspace_id=auth.workspace_id,
        record_uuid=payload.record_uuid,
        assigned_to=payload.assigned_to,
        notes=payload.notes,
        created_by=auth.subject,
        status="pending",
    )
    session.add(item)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="review.create",
        target_uuid=payload.record_uuid,
    )
    await session.commit()
    await session.refresh(item)
    return _ser(item)


@router.get("/reviews")
async def list_reviews(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "reviewer"))],
    review_status: str | None = None,
) -> dict:
    query = select(ReviewQueue).where(ReviewQueue.workspace_id == auth.workspace_id)
    if review_status:
        query = query.where(ReviewQueue.status == review_status)
    query = query.order_by(ReviewQueue.created_at.desc())
    result = await session.execute(query)
    items = list(result.scalars().all())
    return {"results": [_ser(r) for r in items], "count": len(items)}


@router.patch("/reviews/{review_id}")
async def update_review(
    review_id: str,
    payload: ReviewUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "reviewer"))],
) -> dict:
    import uuid as uuid_pkg

    try:
        rid = uuid_pkg.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID.")

    result = await session.execute(
        select(ReviewQueue).where(ReviewQueue.id == rid, ReviewQueue.workspace_id == auth.workspace_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    if payload.status is not None:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")
        item.status = payload.status
        item.reviewed_by = auth.subject
    if payload.notes is not None:
        item.notes = payload.notes
    if payload.assigned_to is not None:
        item.assigned_to = payload.assigned_to

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="review.update",
        target_uuid=str(item.record_uuid),
        details={"new_status": payload.status},
    )
    await session.commit()
    await session.refresh(item)
    return _ser(item)


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    review_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> None:
    import uuid as uuid_pkg

    try:
        rid = uuid_pkg.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID.")

    result = await session.execute(
        select(ReviewQueue).where(ReviewQueue.id == rid, ReviewQueue.workspace_id == auth.workspace_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    await session.delete(item)
    await session.commit()
