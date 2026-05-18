from __future__ import annotations

import csv
import io
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, ensure_workspace_scope, get_client_ip, get_current_auth_context, require_admin, require_role
from app.db.models import ProvenanceRecord, ProvenanceTag
from app.db.session import get_db_session
from app.services.provenance_service import serialize_provenance_record

router = APIRouter(tags=["bulk"])
logger = logging.getLogger(__name__)

MAX_BULK_RECORDS = 500



class BulkDeleteRequest(BaseModel):
    uuids: list[str] = Field(..., min_length=1, max_length=MAX_BULK_RECORDS)
    workspace_id: str = Field(..., alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True)


class BulkTagRequest(BaseModel):
    uuids: list[str] = Field(..., min_length=1, max_length=MAX_BULK_RECORDS)
    tags: list[str] = Field(..., min_length=1)
    workspace_id: str = Field(..., alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True)


class BulkExportRequest(BaseModel):
    uuids: list[str] = Field(..., min_length=1, max_length=MAX_BULK_RECORDS)
    format: str = Field(default="json")
    workspace_id: str = Field(..., alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True)


async def _fetch_records(
    session: AsyncSession,
    uuids: list[str],
    workspace_id: str,
) -> list[ProvenanceRecord]:
    import uuid as uuid_pkg

    parsed_uuids = []
    for u in uuids:
        try:
            parsed_uuids.append(uuid_pkg.UUID(u))
        except (ValueError, TypeError):
            continue

    if not parsed_uuids:
        return []

    result = await session.execute(
        select(ProvenanceRecord).where(
            and_(
                ProvenanceRecord.workspace_id == workspace_id,
                ProvenanceRecord.uuid.in_(parsed_uuids),
            )
        )
    )
    return list(result.scalars().all())


@router.post("/bulk/delete", status_code=status.HTTP_200_OK)
async def bulk_delete(
    payload: BulkDeleteRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Hard-delete multiple provenance records. Admin only."""
    ensure_workspace_scope(auth, payload.workspace_id)

    records = await _fetch_records(session, payload.uuids, auth.workspace_id)
    deleted_uuids = [str(r.uuid) for r in records]

    for record in records:
        session.delete(record)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="record.delete",
        details={"bulk": True, "count": len(deleted_uuids), "uuids": deleted_uuids},
        ip_address=get_client_ip(request),
    )

    await session.commit()

    not_found = [u for u in payload.uuids if u not in deleted_uuids]
    return {
        "deleted": len(deleted_uuids),
        "deleted_uuids": deleted_uuids,
        "not_found": not_found,
    }


@router.post("/bulk/tag", status_code=status.HTTP_200_OK)
async def bulk_tag(
    payload: BulkTagRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "member", "reviewer"))],
) -> dict:
    """Add tags to multiple provenance records."""
    ensure_workspace_scope(auth, payload.workspace_id)

    records = await _fetch_records(session, payload.uuids, auth.workspace_id)
    found_uuids = {str(r.uuid) for r in records}
    tag_values = [t.strip().lower() for t in payload.tags if t.strip()]

    tagged_count = 0
    for record in records:
        record_uuid_str = str(record.uuid)
        for tag_val in tag_values:
            existing = await session.execute(
                select(ProvenanceTag).where(
                    and_(
                        ProvenanceTag.workspace_id == auth.workspace_id,
                        ProvenanceTag.record_uuid == record_uuid_str,
                        ProvenanceTag.tag == tag_val,
                    )
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue
            new_tag = ProvenanceTag(
                workspace_id=auth.workspace_id,
                record_uuid=record_uuid_str,
                tag=tag_val,
                created_by=auth.subject,
            )
            session.add(new_tag)
            tagged_count += 1

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="tag.add",
        details={"bulk": True, "tags": tag_values, "record_count": len(records)},
        ip_address=get_client_ip(request),
    )

    await session.commit()

    not_found = [u for u in payload.uuids if u not in found_uuids]
    return {
        "tagged_records": len(records),
        "tags_added": tagged_count,
        "tags": tag_values,
        "not_found": not_found,
    }


@router.post("/bulk/export", response_model=None)
async def bulk_export(
    payload: BulkExportRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> JSONResponse | StreamingResponse:
    """Export multiple provenance records as JSON or CSV (max 500)."""
    ensure_workspace_scope(auth, payload.workspace_id)

    fmt = payload.format.strip().lower()
    if fmt not in {"json", "csv"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format must be 'json' or 'csv'.",
        )

    records = await _fetch_records(session, payload.uuids, auth.workspace_id)
    serialized = [serialize_provenance_record(r) for r in records]

    if fmt == "json":
        return JSONResponse(content={"results": serialized, "count": len(serialized)})

    # CSV output
    if not serialized:
        return StreamingResponse(
            iter(["uuid,file_path,model_name,timestamp_iso,risk_score\r\n"]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=\"bulk-export.csv\""},
        )

    output = io.StringIO()
    keys = ["uuid", "filePath", "modelName", "timestampIso", "riskScore", "insertedCode"]
    writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in serialized:
        writer.writerow({k: str(row.get(k, "")) for k in keys})

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=\"bulk-export.csv\""},
    )


class BulkFlagRequest(BaseModel):
    uuids: list[str] = Field(..., min_length=1, max_length=MAX_BULK_RECORDS)
    flag_reason: str = Field(default="flagged", alias="flagReason", max_length=256)
    workspace_id: str = Field(..., alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True)


class BulkReviewRequest(BaseModel):
    uuids: list[str] = Field(..., min_length=1, max_length=MAX_BULK_RECORDS)
    assigned_to: str | None = Field(default=None, alias="assignedTo")
    notes: str | None = None
    workspace_id: str = Field(..., alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True)


@router.post("/bulk/flag", status_code=status.HTTP_200_OK)
async def bulk_flag(
    payload: BulkFlagRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "member", "reviewer"))],
) -> dict:
    """Flag multiple records by adding a 'flagged' tag and a reason tag."""
    ensure_workspace_scope(auth, payload.workspace_id)

    records = await _fetch_records(session, payload.uuids, auth.workspace_id)
    found_uuids = {str(r.uuid) for r in records}
    reason_tag = payload.flag_reason.strip().lower().replace(" ", "-")[:64] or "flagged"

    flagged = 0
    for record in records:
        ruuid = str(record.uuid)
        for tag_val in ["flagged", reason_tag]:
            existing = await session.execute(
                select(ProvenanceTag).where(
                    and_(
                        ProvenanceTag.workspace_id == auth.workspace_id,
                        ProvenanceTag.record_uuid == ruuid,
                        ProvenanceTag.tag == tag_val,
                    )
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue
            session.add(ProvenanceTag(
                workspace_id=auth.workspace_id,
                record_uuid=ruuid,
                tag=tag_val,
                created_by=auth.subject,
            ))
            flagged += 1

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="record.flag",
        details={"bulk": True, "reason": reason_tag, "count": len(records)},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    return {
        "flagged": len(records),
        "tags_added": flagged,
        "not_found": [u for u in payload.uuids if u not in found_uuids],
    }


@router.post("/bulk/review", status_code=status.HTTP_200_OK)
async def bulk_review(
    payload: BulkReviewRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "reviewer"))],
) -> dict:
    """Add multiple records to the reviewer queue."""
    ensure_workspace_scope(auth, payload.workspace_id)

    from app.db.models import ReviewQueue

    records = await _fetch_records(session, payload.uuids, auth.workspace_id)
    found_uuids = {str(r.uuid) for r in records}

    from sqlalchemy import exists
    queued = 0
    for record in records:
        record_uuid_str = str(record.uuid)
        already_exists = await session.scalar(
            select(exists().where(
                ReviewQueue.workspace_id == auth.workspace_id,
                ReviewQueue.record_uuid == record_uuid_str,
                ReviewQueue.status == "pending",
            ))
        )
        if already_exists:
            continue
        item = ReviewQueue(
            workspace_id=auth.workspace_id,
            record_uuid=record_uuid_str,
            assigned_to=payload.assigned_to,
            notes=payload.notes,
            created_by=auth.subject,
            status="pending",
        )
        session.add(item)
        queued += 1

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="review.bulk_create",
        details={"count": queued},
        ip_address=get_client_ip(request),
    )
    await session.commit()
    return {
        "queued": queued,
        "not_found": [u for u in payload.uuids if u not in found_uuids],
    }
