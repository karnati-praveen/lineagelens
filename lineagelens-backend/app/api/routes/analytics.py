from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.services.analytics_service import detect_anomalies, get_model_usage, get_risk_trend, get_token_cost

router = APIRouter(tags=["analytics"])
logger = logging.getLogger(__name__)


class RiskTrendRequest(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    date_from: str | None = Field(default=None, alias="dateFrom")
    date_to: str | None = Field(default=None, alias="dateTo")
    bucket: str = Field(default="day")

    model_config = ConfigDict(populate_by_name=True)


class ModelUsageRequest(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    date_from: str | None = Field(default=None, alias="dateFrom")
    date_to: str | None = Field(default=None, alias="dateTo")

    model_config = ConfigDict(populate_by_name=True)


class TokenCostRequest(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    date_from: str | None = Field(default=None, alias="dateFrom")
    date_to: str | None = Field(default=None, alias="dateTo")

    model_config = ConfigDict(populate_by_name=True)


@router.post("/analytics/risk-trend")
async def analytics_risk_trend(
    payload: RiskTrendRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return risk score counts grouped by time bucket."""
    ensure_workspace_scope(auth, payload.workspace_id)

    bucket = payload.bucket.strip().lower()
    if bucket not in {"day", "week", "month"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bucket must be 'day', 'week', or 'month'.",
        )

    rows = await get_risk_trend(
        session=session,
        workspace_id=auth.workspace_id,
        date_from=payload.date_from,
        date_to=payload.date_to,
        bucket=bucket,
    )
    return {"results": rows, "bucket": bucket}


@router.post("/analytics/model-usage")
async def analytics_model_usage(
    payload: ModelUsageRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return per-model usage statistics."""
    ensure_workspace_scope(auth, payload.workspace_id)

    rows = await get_model_usage(
        session=session,
        workspace_id=auth.workspace_id,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    return {"results": rows}


@router.post("/analytics/token-cost")
async def analytics_token_cost(
    payload: TokenCostRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return aggregated token count and cost statistics."""
    ensure_workspace_scope(auth, payload.workspace_id)

    data = await get_token_cost(
        session=session,
        workspace_id=auth.workspace_id,
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    return data


class AnomalyRequest(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    date_from: str | None = Field(default=None, alias="dateFrom")
    date_to: str | None = Field(default=None, alias="dateTo")
    z_threshold: float = Field(default=2.0, alias="zThreshold", ge=1.0, le=5.0)

    model_config = ConfigDict(populate_by_name=True)


@router.post("/analytics/anomaly")
async def analytics_anomaly(
    payload: AnomalyRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Detect anomalous risk scores and volume spikes."""
    ensure_workspace_scope(auth, payload.workspace_id)

    return await detect_anomalies(
        session=session,
        workspace_id=auth.workspace_id,
        date_from=payload.date_from,
        date_to=payload.date_to,
        z_threshold=payload.z_threshold,
    )
