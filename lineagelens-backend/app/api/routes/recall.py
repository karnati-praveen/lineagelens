from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, require_role
from app.db.models import RecallCampaign
from app.db.session import get_db_session
from app.services.recall_service import (
    _campaign_to_dict,
    _flag_records,
    clear_records,
    close_recall,
    compute_blast_radius,
    find_affected_records,
    open_recall,
    quarantine_records,
)

router = APIRouter(
    prefix="/recall",
    tags=["recall"],
    dependencies=[Depends(require_non_solo)],
)
logger = logging.getLogger(__name__)


# ─── Schemas ──────────────────────────────────────────────────────────────────

class RecallCriteria(BaseModel):
    model: str | None = None
    modelVersion: str | None = Field(default=None, alias="modelVersion")
    promptPatternRegex: str | None = Field(default=None, alias="promptPatternRegex")
    dateFrom: datetime | None = Field(default=None, alias="dateFrom")
    dateTo: datetime | None = Field(default=None, alias="dateTo")
    recordUuid: str | None = Field(default=None, alias="recordUuid")

    model_config = ConfigDict(populate_by_name=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_neo4j(request):
    from fastapi import Request as _Request
    return getattr(request.app.state, "neo4j_service", None)


async def _get_campaign_or_404(
    session: AsyncSession, campaign_id: int, workspace_id: str
) -> RecallCampaign:
    result = await session.execute(
        select(RecallCampaign).where(
            RecallCampaign.id == campaign_id,
            RecallCampaign.workspace_id == workspace_id,
        )
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Recall campaign not found.")
    return campaign


def _criteria_to_kwargs(criteria: RecallCriteria) -> dict:
    return {
        "model": criteria.model,
        "model_version": criteria.modelVersion,
        "prompt_pattern_regex": criteria.promptPatternRegex,
        "date_from": criteria.dateFrom,
        "date_to": criteria.dateTo,
        "record_uuid": criteria.recordUuid,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/preview")
async def preview_recall(
    criteria: RecallCriteria,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
    request: "Request",
) -> dict:
    """Dry-run: return matched records and blast-radius count without mutating state."""
    from fastapi import Request  # local to keep import clean

    matched = await find_affected_records(
        session, auth.workspace_id, **_criteria_to_kwargs(criteria)
    )
    matched_uuids = [str(r.uuid) for r in matched]

    neo4j = _get_neo4j(request)
    blast_uuids = await compute_blast_radius(neo4j, matched_uuids, auth.workspace_id)

    return {
        "matchedCount": len(matched),
        "blastRadiusCount": len(blast_uuids),
        "matchedUuids": matched_uuids,
        "blastUuids": blast_uuids,
        "criteria": criteria.model_dump(by_alias=True, exclude_none=True),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_recall(
    criteria: RecallCriteria,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
    request: "Request",
) -> dict:
    """Open a recall campaign and flag matched records."""
    from fastapi import Request

    matched = await find_affected_records(
        session, auth.workspace_id, **_criteria_to_kwargs(criteria)
    )
    matched_uuids = [str(r.uuid) for r in matched]

    campaign = await open_recall(
        session,
        workspace_id=auth.workspace_id,
        created_by=auth.subject,
        criteria_json=criteria.model_dump(by_alias=True, exclude_none=True),
        matched_count=len(matched_uuids),
    )

    if matched_uuids:
        await _flag_records(session, auth.workspace_id, auth.subject, matched_uuids, campaign.id)
        await session.commit()

    return _campaign_to_dict(campaign)


@router.post("/{campaign_id}/quarantine")
async def quarantine_campaign(
    campaign_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
    request: "Request",
) -> dict:
    """Quarantine all records flagged by this campaign plus their blast-radius descendants."""
    from fastapi import Request

    campaign = await _get_campaign_or_404(session, campaign_id, auth.workspace_id)
    if campaign.status != "open":
        raise HTTPException(status_code=409, detail="Campaign is not open.")

    criteria = RecallCriteria(**campaign.criteria_json)
    matched = await find_affected_records(
        session, auth.workspace_id, **_criteria_to_kwargs(criteria)
    )
    matched_uuids = [str(r.uuid) for r in matched]

    neo4j = _get_neo4j(request)
    blast_uuids = await compute_blast_radius(neo4j, matched_uuids, auth.workspace_id)

    count = await quarantine_records(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        campaign_id=campaign_id,
        record_uuids=matched_uuids,
        blast_uuids=blast_uuids,
    )

    return {
        "quarantinedCount": count,
        "blastCount": len(blast_uuids),
        "campaign": _campaign_to_dict(campaign),
    }


@router.post("/{campaign_id}/clear")
async def clear_campaign(
    campaign_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    """Clear quarantine for all records in this campaign."""
    campaign = await _get_campaign_or_404(session, campaign_id, auth.workspace_id)
    count = await clear_records(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        campaign_id=campaign_id,
    )
    return {"clearedCount": count, "campaign": _campaign_to_dict(campaign)}


@router.post("/{campaign_id}/close")
async def close_campaign(
    campaign_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    """Mark a recall campaign as closed."""
    campaign = await _get_campaign_or_404(session, campaign_id, auth.workspace_id)
    if campaign.status == "closed":
        raise HTTPException(status_code=409, detail="Campaign is already closed.")
    campaign = await close_recall(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        campaign=campaign,
    )
    return _campaign_to_dict(campaign)


@router.get("")
async def list_recalls(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    stmt = (
        select(RecallCampaign)
        .where(RecallCampaign.workspace_id == auth.workspace_id)
        .order_by(desc(RecallCampaign.created_at))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    items = result.scalars().all()
    return {
        "items": [_campaign_to_dict(c) for c in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{campaign_id}")
async def get_recall(
    campaign_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    campaign = await _get_campaign_or_404(session, campaign_id, auth.workspace_id)
    return _campaign_to_dict(campaign)
