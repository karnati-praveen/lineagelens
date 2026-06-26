from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, get_client_ip, require_admin
from app.db.session import get_db_session
from app.services.provenance_service import get_provenance_by_uuid
from app.services.record_lifecycle_service import apply_lifecycle_event

router = APIRouter(tags=["deletion"])
logger = logging.getLogger(__name__)



@router.delete("/provenance/{record_uuid}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_provenance_record(
    record_uuid: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
    reason: str | None = None,
    policy_ref: str | None = None,
) -> None:
    """Delete a provenance record via a signed deletion tombstone. Admin only.

    The row is NOT physically removed — doing so would dangle the next record's
    ``prev_hash`` and break the append-only chain (PART 2 #11). Instead all
    content is scrubbed, a signed ``RecordLifecycleEvent`` is written, and the
    chain link is preserved so the verifier reports ``validly_deleted``.
    """
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

    if record.lifecycle_state == "deleted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Record has already been deleted.",
        )

    await apply_lifecycle_event(
        session,
        record,
        event_type="deletion",
        authorized_by=auth.subject,
        reason=reason,
        policy_ref=policy_ref,
    )

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="record.delete",
        target_uuid=record_uuid,
        details={"file_path": record.file_path, "tombstone": True},
        ip_address=get_client_ip(request),
    )

    await session.commit()


@router.patch("/provenance/{record_uuid}/redact")
async def redact_provenance_record(
    record_uuid: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
    reason: str | None = None,
    policy_ref: str | None = None,
) -> dict:
    """Redact a provenance record (clears sensitive fields) via a signed event. Admin only.

    Sensitive prompt/response/context are scrubbed but the chain stays intact
    and the operation is attested by a signed ``RecordLifecycleEvent`` so the
    verifier reports ``validly_redacted`` rather than ``tampered`` (PART 2 #10).
    """
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

    await apply_lifecycle_event(
        session,
        record,
        event_type="redaction",
        authorized_by=auth.subject,
        reason=reason,
        policy_ref=policy_ref,
    )

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
        "lifecycle_state": record.lifecycle_state,
        "file_path": record.file_path,
        "timestamp_iso": record.timestamp_iso.isoformat(),
        "risk_score": record.risk_score,
        "inserted_code": record.inserted_code,
    }
