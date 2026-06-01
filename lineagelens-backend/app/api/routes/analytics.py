from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.models import ProvenanceRecord
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
    request: Request,
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
        is_sqlite=request.app.state.settings.is_sqlite,
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
    request: Request,
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


@router.get("/analytics/routing-savings")
async def analytics_routing_savings(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return the estimated USD cost saved by dynamic model routing over the last 30 days.

    Sums the ``savings_estimate_usd`` field from each provenance record's
    ``routing_decision`` JSON column.  Records without a routing decision
    (i.e. not routed, or routed before this feature was deployed) contribute 0.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)

    result = await session.execute(
        select(ProvenanceRecord.routing_decision)
        .where(ProvenanceRecord.workspace_id == auth.workspace_id)
        .where(ProvenanceRecord.created_at >= cutoff)
        .where(ProvenanceRecord.routing_decision.isnot(None))
    )
    rows = result.all()

    total: float = 0.0
    routed_count: int = 0
    for (rd,) in rows:
        if isinstance(rd, dict):
            savings = rd.get("savings_estimate_usd", 0.0)
            if isinstance(savings, (int, float)):
                total += float(savings)
            routed_count += 1

    return {
        "savings_usd_30d": round(total, 6),
        "routed_requests_30d": routed_count,
    }
