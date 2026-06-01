from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import Text, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProvenanceRecord

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _base_filters(workspace_id: str, date_from: str | None, date_to: str | None) -> list:
    filters: list = [ProvenanceRecord.workspace_id == workspace_id]
    dt_from = _parse_dt(date_from)
    dt_to = _parse_dt(date_to)
    if dt_from:
        filters.append(ProvenanceRecord.timestamp_iso >= dt_from)
    if dt_to:
        filters.append(ProvenanceRecord.timestamp_iso <= dt_to)
    return filters


_BUCKET_MAP = {
    "day": "day",
    "week": "week",
    "month": "month",
}


_SQLITE_FMT = {"day": "%Y-%m-%d", "week": "%Y-%W", "month": "%Y-%m"}


async def get_risk_trend(
    session: AsyncSession,
    workspace_id: str,
    date_from: str | None,
    date_to: str | None,
    bucket: str,
    is_sqlite: bool = False,
) -> list[dict]:
    """Return risk counts grouped by time bucket and risk band."""
    trunc = _BUCKET_MAP.get(bucket, "day")
    filters = _base_filters(workspace_id, date_from, date_to)

    if is_sqlite:
        fmt = _SQLITE_FMT.get(trunc, "%Y-%m-%d")
        period_expr = func.strftime(fmt, ProvenanceRecord.timestamp_iso).label("period")
    else:
        period_expr = func.date_trunc(trunc, ProvenanceRecord.timestamp_iso).label("period")

    critical_expr = func.count(
        case((ProvenanceRecord.risk_score >= 85, 1))
    ).label("critical")
    high_expr = func.count(
        case((and_(ProvenanceRecord.risk_score >= 65, ProvenanceRecord.risk_score <= 84), 1))
    ).label("high")
    medium_expr = func.count(
        case((and_(ProvenanceRecord.risk_score >= 35, ProvenanceRecord.risk_score <= 64), 1))
    ).label("medium")
    low_expr = func.count(
        case((and_(ProvenanceRecord.risk_score >= 0, ProvenanceRecord.risk_score <= 34), 1))
    ).label("low")

    stmt = (
        select(period_expr, critical_expr, high_expr, medium_expr, low_expr)
        .where(and_(*filters))
        .group_by(period_expr)
        .order_by(period_expr)
    )

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "period": str(row.period)[:10] if row.period else None,
            "critical": row.critical,
            "high": row.high,
            "medium": row.medium,
            "low": row.low,
        }
        for row in rows
    ]


async def get_model_usage(
    session: AsyncSession,
    workspace_id: str,
    date_from: str | None,
    date_to: str | None,
) -> list[dict]:
    """Return per-model usage stats."""
    filters = _base_filters(workspace_id, date_from, date_to)

    stmt = (
        select(
            ProvenanceRecord.model_name,
            func.count(ProvenanceRecord.id).label("record_count"),
            func.avg(ProvenanceRecord.risk_score).label("avg_risk_score"),
            func.sum(ProvenanceRecord.token_count).label("total_tokens"),
        )
        .where(and_(*filters))
        .group_by(ProvenanceRecord.model_name)
        .order_by(func.count(ProvenanceRecord.id).desc())
    )

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "model_name": row.model_name,
            "record_count": row.record_count,
            "avg_risk_score": round(float(row.avg_risk_score), 2) if row.avg_risk_score is not None else None,
            "total_tokens": row.total_tokens,
        }
        for row in rows
    ]


async def get_token_cost(
    session: AsyncSession,
    workspace_id: str,
    date_from: str | None,
    date_to: str | None,
) -> dict:
    """Return aggregated token and cost stats."""
    filters = _base_filters(workspace_id, date_from, date_to)

    # Overall totals
    total_stmt = select(
        func.sum(ProvenanceRecord.token_count).label("total_tokens"),
        func.sum(ProvenanceRecord.cost_usd).label("total_cost_usd"),
    ).where(and_(*filters))

    total_result = await session.execute(total_stmt)
    total_row = total_result.one()

    # By model
    model_stmt = (
        select(
            ProvenanceRecord.model_name,
            func.sum(ProvenanceRecord.token_count).label("tokens"),
            func.sum(ProvenanceRecord.cost_usd).label("cost_usd"),
            func.count(ProvenanceRecord.id).label("record_count"),
        )
        .where(and_(*filters))
        .group_by(ProvenanceRecord.model_name)
        .order_by(func.sum(ProvenanceRecord.token_count).desc().nulls_last())
    )
    model_result = await session.execute(model_stmt)
    model_rows = model_result.all()

    # By user (user_id)
    user_stmt = (
        select(
            ProvenanceRecord.user_id.cast(Text).label("user_id"),
            func.sum(ProvenanceRecord.token_count).label("tokens"),
            func.sum(ProvenanceRecord.cost_usd).label("cost_usd"),
            func.count(ProvenanceRecord.id).label("record_count"),
        )
        .where(and_(*filters))
        .group_by(ProvenanceRecord.user_id)
        .order_by(func.sum(ProvenanceRecord.token_count).desc().nulls_last())
    )
    user_result = await session.execute(user_stmt)
    user_rows = user_result.all()

    return {
        "total_tokens": total_row.total_tokens,
        "total_cost_usd": round(float(total_row.total_cost_usd), 6) if total_row.total_cost_usd is not None else None,
        "by_model": [
            {
                "model_name": r.model_name,
                "tokens": r.tokens,
                "cost_usd": round(float(r.cost_usd), 6) if r.cost_usd is not None else None,
                "record_count": r.record_count,
            }
            for r in model_rows
        ],
        "by_user": [
            {
                "user_id": r.user_id,
                "tokens": r.tokens,
                "cost_usd": round(float(r.cost_usd), 6) if r.cost_usd is not None else None,
                "record_count": r.record_count,
            }
            for r in user_rows
        ],
    }


async def detect_anomalies(
    session: AsyncSession,
    *,
    workspace_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    z_threshold: float = 2.0,
) -> dict:
    """
    Detect anomalous risk scores and insertion-volume spikes.

    Returns:
    {
        "risk_anomalies": [...records where risk_score deviates > z_threshold std above mean],
        "volume_spikes": [...date buckets where daily count > mean + z_threshold * stddev],
        "stats": {"mean_risk": float, "stddev_risk": float, "mean_daily_volume": float}
    }
    """
    from datetime import timezone

    filters = [
        ProvenanceRecord.workspace_id == workspace_id,
        ProvenanceRecord.risk_score.is_not(None),
    ]
    if date_from:
        try:
            filters.append(
                ProvenanceRecord.timestamp_iso
                >= datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
    if date_to:
        try:
            filters.append(
                ProvenanceRecord.timestamp_iso
                <= datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass

    # Get mean and stddev of risk scores
    stats_result = await session.execute(
        select(
            func.avg(ProvenanceRecord.risk_score).label("mean_risk"),
            func.stddev_pop(ProvenanceRecord.risk_score).label("stddev_risk"),
        ).where(and_(*filters))
    )
    stats_row = stats_result.one_or_none()
    mean_risk = float(stats_row.mean_risk or 0) if stats_row else 0.0
    stddev_risk = float(stats_row.stddev_risk or 0) if stats_row else 0.0
    threshold_risk = mean_risk + z_threshold * stddev_risk

    # Find records with risk_score above threshold
    risk_anomalies = []
    if stddev_risk > 0:
        anomaly_result = await session.execute(
            select(
                ProvenanceRecord.uuid,
                ProvenanceRecord.risk_score,
                ProvenanceRecord.file_path,
                ProvenanceRecord.model_name,
                ProvenanceRecord.timestamp_iso,
            )
            .where(and_(*filters, ProvenanceRecord.risk_score >= threshold_risk))
            .order_by(ProvenanceRecord.risk_score.desc())
            .limit(50)
        )
        for row in anomaly_result.all():
            risk_anomalies.append({
                "uuid": str(row.uuid),
                "riskScore": row.risk_score,
                "filePath": row.file_path,
                "modelName": row.model_name,
                "timestamp": row.timestamp_iso.isoformat() if row.timestamp_iso else None,
                "zScore": round((row.risk_score - mean_risk) / stddev_risk, 2) if stddev_risk > 0 else 0,
            })

    # Volume spike detection: count records per day
    volume_filters = [ProvenanceRecord.workspace_id == workspace_id]
    if date_from:
        try:
            volume_filters.append(
                ProvenanceRecord.timestamp_iso
                >= datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
    if date_to:
        try:
            volume_filters.append(
                ProvenanceRecord.timestamp_iso
                <= datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass

    _day_expr = func.date(ProvenanceRecord.timestamp_iso).label("day")
    vol_result = await session.execute(
        select(
            _day_expr,
            func.count(ProvenanceRecord.id).label("count"),
        )
        .where(and_(*volume_filters))
        .group_by(func.date(ProvenanceRecord.timestamp_iso))
        .order_by(func.date(ProvenanceRecord.timestamp_iso))
    )
    volume_rows = vol_result.all()
    daily_counts = [r.count for r in volume_rows]
    mean_vol = sum(daily_counts) / len(daily_counts) if daily_counts else 0
    stddev_vol = (
        (sum((c - mean_vol) ** 2 for c in daily_counts) / len(daily_counts)) ** 0.5
        if daily_counts
        else 0
    )
    vol_threshold = mean_vol + z_threshold * stddev_vol

    volume_spikes = []
    for row in volume_rows:
        if row.count >= vol_threshold and stddev_vol > 0:
            volume_spikes.append({
                "day": str(row.day)[:10] if row.day else None,
                "count": row.count,
                "zScore": round((row.count - mean_vol) / stddev_vol, 2) if stddev_vol > 0 else 0,
            })

    return {
        "risk_anomalies": risk_anomalies,
        "volume_spikes": volume_spikes,
        "stats": {
            "mean_risk": round(mean_risk, 2),
            "stddev_risk": round(stddev_risk, 2),
            "z_threshold": z_threshold,
            "mean_daily_volume": round(mean_vol, 2),
            "stddev_daily_volume": round(stddev_vol, 2),
        },
    }
