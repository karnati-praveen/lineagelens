from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import SearchRequest
from app.services.insights_service import get_insights_dashboard_payload


router = APIRouter(tags=["report"])


@router.get("/report/usage", dependencies=[Depends(require_non_solo)])
async def get_usage_report(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    date_from: datetime | None = Query(default=None, alias="dateFrom"),
    date_to: datetime | None = Query(default=None, alias="dateTo"),
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
    medium_risk = max(0, high_risk - critical)

    risk_distribution = {
        "critical": critical,
        "high": max(0, high_risk - critical),
        "medium": _count_risk_level(insights, "medium"),
        "low": _count_risk_level(insights, "low"),
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
                "linesAdded": m.get("netLinesAdded", 0),
                "aiShare": round(
                    m.get("recordCount", 0) / total if total else 0,
                    3,
                ),
            }
            for m in member_stats
        ],
        "complianceStatus": [
            {
                "control": c.get("name", ""),
                "status": c.get("status", ""),
                "metric": c.get("metric", ""),
            }
            for c in compliance
        ],
        "weeklyTrend": risk_trends,
        "warnings": insights.get("warnings", []),
    }


def _score_to_label(score: float) -> str:
    if score >= 0.85:
        return "critical"
    if score >= 0.65:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _count_risk_level(insights: dict, level: str) -> int:
    for trend_item in insights.get("riskTrends", []):
        counts = trend_item.get("riskCounts", {})
        if counts:
            pass
    high_risk_records = insights.get("highRiskRecords", [])
    total = insights.get("summary", {}).get("totalRecords", 0)
    high = insights.get("summary", {}).get("highRiskRecords", 0)
    critical = insights.get("summary", {}).get("criticalRecords", 0)
    if level == "medium":
        return max(0, high - critical)
    if level == "low":
        return max(0, total - high)
    return 0
