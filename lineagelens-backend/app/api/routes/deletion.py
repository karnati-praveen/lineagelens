from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_client_ip, require_admin
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.services.provenance_service import get_provenance_by_uuid, serialize_provenance_record

router = APIRouter(tags=["deletion"])
logger = logging.getLogger(__name__)



@router.delete("/provenance/{record_uuid}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_provenance_record(
    record_uuid: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> None:
    """Hard-delete a provenance record. Admin only."""
    record = await get_provenance_by_uuid(
        session=session,
        record_uuid=record_uuid,
        workspace_id=auth.workspace_id,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )

    await session.delete(record)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="record.delete",
        target_uuid=record_uuid,
        details={"file_path": record.file_path},
        ip_address=get_client_ip(request),
    )

    await session.commit()


@router.patch("/provenance/{record_uuid}/redact")
async def redact_provenance_record(
    record_uuid: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Soft-redact a provenance record (clears sensitive fields). Admin only."""
    record = await get_provenance_by_uuid(
        session=session,
        record_uuid=record_uuid,
        workspace_id=auth.workspace_id,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )

    if record.is_redacted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Record has already been redacted.",
        )

    record.prompt_messages = None
    record.raw_model_response = None
    record.surrounding_context = None
    record.context_snapshot = None
    record.is_redacted = True

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="record.redact",
        target_uuid=record_uuid,
        details={"file_path": record.file_path},
        ip_address=get_client_ip(request),
    )

    await session.commit()
    await session.refresh(record)

    return {
        "uuid": str(record.uuid),
        "is_redacted": record.is_redacted,
        "file_path": record.file_path,
        "timestamp_iso": record.timestamp_iso.isoformat(),
        "risk_score": record.risk_score,
        "inserted_code": record.inserted_code,
    }
