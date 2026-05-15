from __future__ import annotations

import logging
import uuid as uuid_pkg
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.services.quality_service import compute_code_metrics

router = APIRouter(tags=["quality"])
logger = logging.getLogger(__name__)


class BatchQualityRequest(BaseModel):
    uuids: list[str] = Field(..., min_length=1, max_length=100)
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True)


@router.get("/quality/{record_uuid}")
async def get_record_quality(
    record_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Compute code quality metrics for a single provenance record's inserted code."""
    try:
        parsed_uuid = uuid_pkg.UUID(record_uuid)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    result = await session.execute(
        select(
            ProvenanceRecord.uuid,
            ProvenanceRecord.inserted_code,
            ProvenanceRecord.file_path,
            ProvenanceRecord.model_name,
            ProvenanceRecord.risk_score,
        )
        .where(
            ProvenanceRecord.uuid == parsed_uuid,
            ProvenanceRecord.workspace_id == auth.workspace_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")

    metrics = compute_code_metrics(row.inserted_code or "", row.file_path or "")
    return {
        "uuid": str(row.uuid),
        "file_path": row.file_path,
        "model_name": row.model_name,
        "risk_score": row.risk_score,
        "metrics": metrics,
    }


@router.post("/quality/batch")
async def batch_quality(
    payload: BatchQualityRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Compute quality metrics for multiple records and return comparative analysis."""
    parsed_uuids = []
    for u in payload.uuids:
        try:
            parsed_uuids.append(uuid_pkg.UUID(u))
        except ValueError:
            continue

    if not parsed_uuids:
        return {"results": [], "summary": {"count": 0}}

    result = await session.execute(
        select(
            ProvenanceRecord.uuid,
            ProvenanceRecord.inserted_code,
            ProvenanceRecord.file_path,
            ProvenanceRecord.model_name,
            ProvenanceRecord.risk_score,
        )
        .where(
            ProvenanceRecord.uuid.in_(parsed_uuids),
            ProvenanceRecord.workspace_id == auth.workspace_id,
        )
    )
    rows = result.all()

    results = []
    for row in rows:
        metrics = compute_code_metrics(row.inserted_code or "", row.file_path or "")
        results.append({
            "uuid": str(row.uuid),
            "file_path": row.file_path,
            "model_name": row.model_name,
            "risk_score": row.risk_score,
            "metrics": metrics,
        })

    if results:
        ml = [r["metrics"] for r in results]
        summary = {
            "count": len(results),
            "avgCyclomaticComplexity": round(
                sum(m["cyclomaticComplexity"] for m in ml) / len(ml), 1
            ),
            "avgMaintainabilityScore": round(
                sum(m["maintainabilityScore"] for m in ml) / len(ml), 1
            ),
            "avgCommentRatio": round(
                sum(m["commentRatio"] for m in ml) / len(ml), 3
            ),
            "totalCodeLines": sum(m["codeLines"] for m in ml),
            "highComplexityCount": sum(1 for m in ml if m["cyclomaticComplexity"] > 10),
            "lowMaintainabilityCount": sum(1 for m in ml if m["maintainabilityScore"] < 50),
        }
    else:
        summary = {"count": 0}

    return {"results": results, "summary": summary}
