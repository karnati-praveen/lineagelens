from __future__ import annotations

"""AI Change Exposure Exchange Routes (OpenVEX & OSV)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, get_current_auth_context
from app.db.session import get_db_session
from app.services.exposure_service import build_openvex_advisory, build_osv_advisory

router = APIRouter(
    prefix="/exposure",
    tags=["exposure"],
    dependencies=[Depends(require_non_solo)],
)
logger = logging.getLogger(__name__)


@router.get("/openvex")
async def get_openvex_exposure(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    campaign_id: int | None = Query(default=None, alias="campaignId"),
) -> dict:
    """Export machine-readable OpenVEX advisory statements for recalled or quarantined models."""
    doc = await build_openvex_advisory(session, auth.workspace_id, campaign_id=campaign_id)
    return doc.model_dump(by_alias=True, exclude_none=True)


@router.get("/osv")
async def get_osv_exposure(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    campaign_id: int | None = Query(default=None, alias="campaignId"),
) -> list[dict]:
    """Export machine-readable OSV (Open Source Vulnerability) advisories."""
    docs = await build_osv_advisory(session, auth.workspace_id, campaign_id=campaign_id)
    return [d.model_dump(by_alias=True, exclude_none=True) for d in docs]
