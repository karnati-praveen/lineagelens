from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, get_current_auth_context
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.schemas.provenance import SearchRequest
from app.services.insights_service import get_insights_dashboard_payload


router = APIRouter(tags=["report"])


@router.get("/report/usage", dependencies=[Depends(require_non_solo)])
async def get_usage_report(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    date_from: Annotated[datetime | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[datetime | None, Query(alias="dateTo")] = None,
) -> dict:
    """AI usage report — % AI-written code, risky files, model breakdown, developer stats.

    Designed for engineering leads and compliance teams who need a quick
    snapshot of AI activity in the workspace. Results are cached for 5 minutes
    alongside the governance dashboard.
    """
    search = SearchRequest(
        workspace_id=auth.workspace_id,
        date_from=date_from,
        date_to=date_to,
    )

    insights = await get_insights_dashboard_payload(
        session=session,
        search=search,
        workspace_id=auth.workspace_id,
    )

    summary = insights.get("summary", {})
    model_analytics = insights.get("modelAnalytics", [])
    hotspots = insights.get("hotspots", [])
    member_stats = insights.get("memberStats", [])
    compliance = insights.get("complianceControls", [])
    risk_trends = insights.get("riskTrends", [])

    total = summary.get("totalRecords", 0)
    high_risk = summary.get("highRiskRecords", 0)
    critical = summary.get("criticalRecords", 0)

    risk_distribution = await _fetch_risk_distribution(session, auth.workspace_id, date_from, date_to)
    if risk_distribution is None:
        # Fall back to summary-derived counts when no stored risk_scores exist
        risk_distribution = {
            "critical": critical,
            "high": max(0, high_risk - critical),
            "medium": 0,
            "low": max(0, total - high_risk),
            "unscored": total,
        }

    return {
        "generatedAt": insights.get("generatedAtIso"),
        "period": {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
        },
        "workspace": auth.workspace_id,
        "summary": {
            "totalInsertions": total,
            "totalAiLines": summary.get("totalNetAddedLines", 0),
            "uniqueFiles": summary.get("uniqueFiles", 0),
            "uniqueModels": summary.get("uniqueModels", 0),
            "agentSessions": summary.get("uniqueAgentSessions", 0),
            "teamMembers": len(member_stats),
            "promptCaptureRate": summary.get("promptCaptureRate", 0),
            "avgRiskScore": summary.get("avgRiskScore", 0),
        },
        "riskDistribution": risk_distribution,
        "modelUsage": [
            {
                "model": m.get("model") or m.get("modelName", "unknown"),
                "insertions": m.get("recordCount", 0),
                "linesAdded": m.get("netLinesAdded", 0),
                "avgRisk": round(m.get("avgRiskScore", 0), 3),
                "highRiskCount": m.get("highRiskCount", 0),
            }
            for m in model_analytics[:10]
        ],
        "topRiskyFiles": [
            {
                "filePath": h.get("filePath") or h.get("file", ""),
                "insertions": h.get("recordCount", 0),
                "avgRisk": round(h.get("avgRiskScore", 0), 3),
                "riskLevel": _score_to_label(h.get("avgRiskScore", 0)),
            }
            for h in hotspots[:10]
        ],
        "developerBreakdown": [
            {
                "username": m.get("username", ""),
                "insertions": m.get("recordCount", 0),
                "linesAdded": m.get("netAddedLines", 0),
                "aiShare": round(
                    m.get("recordCount", 0) / total if total else 0,
                    3,
                ),
            }
            for m in member_stats
        ],
        "complianceStatus": [
            {
                "control": c.get("title", c.get("name", "")),
                "status": c.get("status", ""),
                "metric": c.get("metric", ""),
            }
            for c in compliance
        ],
        "weeklyTrend": risk_trends,
        "warnings": insights.get("warnings", []),
    }


def _score_to_label(score: float) -> str:
    """Convert a 0–100 risk score to a human-readable label.

    Thresholds mirror ``CRITICAL_RISK_THRESHOLD`` (85) and
    ``HIGH_RISK_THRESHOLD`` (65) defined in insights_service.py.
    """
    if score >= 85:
        return "critical"
    if score >= 65:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


async def _fetch_risk_distribution(
    session: AsyncSession,
    workspace_id: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict | None:
    """Return per-band record counts using stored risk_score values.

    Executes a single aggregate query using SQLAlchemy func.count with .filter()
    clauses.  Returns None if no scored records exist (caller should fall back
    to summary-derived counts).

    SQL equivalent:
        SELECT
          COUNT(*) FILTER (WHERE risk_score >= 85) AS critical,
          COUNT(*) FILTER (WHERE risk_score >= 65 AND risk_score < 85) AS high,
          COUNT(*) FILTER (WHERE risk_score >= 35 AND risk_score < 65) AS medium,
          COUNT(*) FILTER (WHERE risk_score < 35) AS low,
          COUNT(*) FILTER (WHERE risk_score IS NULL) AS unscored
        FROM provenance_records WHERE workspace_id = :wid
    """
    base_filters = [ProvenanceRecord.workspace_id == workspace_id]
    if date_from:
        base_filters.append(ProvenanceRecord.timestamp_iso >= date_from)
    if date_to:
        base_filters.append(ProvenanceRecord.timestamp_iso <= date_to)

    rs = ProvenanceRecord.risk_score
    stmt = select(
        func.count(rs).filter(rs >= 85).label("critical"),
        func.count(rs).filter(and_(rs >= 65, rs < 85)).label("high"),
        func.count(rs).filter(and_(rs >= 35, rs < 65)).label("medium"),
        func.count(rs).filter(rs < 35).label("low"),
        func.count(ProvenanceRecord.id).filter(rs.is_(None)).label("unscored"),
    ).where(and_(*base_filters))

    result = await session.execute(stmt)
    row = result.one_or_none()
    if row is None:
        return None

    scored_total = (row.critical or 0) + (row.high or 0) + (row.medium or 0) + (row.low or 0)
    if scored_total == 0:
        return None

    return {
        "critical": row.critical or 0,
        "high": row.high or 0,
        "medium": row.medium or 0,
        "low": row.low or 0,
        "unscored": row.unscored or 0,
    }
