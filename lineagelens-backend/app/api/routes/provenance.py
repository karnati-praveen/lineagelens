from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import (
    AuthContext,
    build_record_visibility_clause,
    get_current_auth_context,
    get_verified_user_role,
)
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.schemas.provenance import ProvenanceResponse
from app.services.provenance_service import get_provenance_by_uuid, serialize_provenance_record


router = APIRouter(tags=["provenance"])


@router.get("/provenance")
async def list_provenance_records(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """List provenance records for the workspace with limit/offset pagination."""
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )
    filters = [ProvenanceRecord.workspace_id == auth.workspace_id]

    count_result = await session.execute(
        select(func.count()).select_from(ProvenanceRecord).where(and_(*filters, access_clause))
    )
    total = count_result.scalar_one_or_none() or 0

    result = await session.execute(
        select(ProvenanceRecord)
        .where(and_(*filters, access_clause))
        .order_by(desc(ProvenanceRecord.timestamp_iso), ProvenanceRecord.uuid)
        .offset(offset)
        .limit(limit)
    )
    records = result.scalars().all()

    return {
        "results": [serialize_provenance_record(r) for r in records],
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": (offset + len(records)) < total,
    }


@router.get("/provenance/{record_uuid}")
async def get_provenance_record(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> ProvenanceResponse:
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )
    record = await get_provenance_by_uuid(
        session=session,
        record_uuid=record_uuid,
        workspace_id=auth.workspace_id,
        access_filters=[access_clause],
    )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="record.view",
        target_uuid=record_uuid,
    )
    await session.commit()

    return ProvenanceResponse(uuid=str(record.uuid), record=serialize_provenance_record(record))
