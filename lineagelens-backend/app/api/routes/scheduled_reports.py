from __future__ import annotations

import logging
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, require_admin
from app.db.models import ScheduledReport
from app.db.session import get_db_session, get_session_factory

router = APIRouter(prefix="/scheduled-reports", tags=["scheduled-reports"])
logger = logging.getLogger(__name__)


class ScheduledReportCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    report_type: str = Field(..., pattern="^(usage|risk_summary|team_activity)$")
    frequency: str = Field(default="weekly", pattern="^(daily|weekly|monthly)$")
    recipients: list[str] = Field(default_factory=list, max_length=50)
    config: dict = Field(default_factory=dict)
    enabled: bool = True

    model_config = ConfigDict(populate_by_name=True)


class ScheduledReportUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    frequency: str | None = Field(default=None, pattern="^(daily|weekly|monthly)$")
    recipients: list[str] | None = None
    config: dict | None = None
    enabled: bool | None = None

    model_config = ConfigDict(populate_by_name=True)


def _next_run(frequency: str) -> datetime:
    now = datetime.now(UTC)
    if frequency == "daily":
        return now + timedelta(days=1)
    if frequency == "monthly":
        return now + timedelta(days=30)
    return now + timedelta(weeks=1)


def _ser(r: ScheduledReport) -> dict:
    return {
        "id": str(r.id),
        "workspaceId": r.workspace_id,
        "name": r.name,
        "reportType": r.report_type,
        "frequency": r.frequency,
        "recipients": r.recipients,
        "config": r.config,
        "enabled": r.enabled,
        "lastRunAt": r.last_run_at.isoformat() if r.last_run_at else None,
        "nextRunAt": r.next_run_at.isoformat() if r.next_run_at else None,
        "createdAt": r.created_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_scheduled_report(
    payload: ScheduledReportCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Create a scheduled report digest (admin only)."""
    r = ScheduledReport(
        workspace_id=auth.workspace_id,
        name=payload.name,
        report_type=payload.report_type,
        frequency=payload.frequency,
        recipients=payload.recipients,
        config=payload.config,
        enabled=payload.enabled,
        created_by=auth.subject,
        next_run_at=_next_run(payload.frequency),
    )
    session.add(r)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="scheduled_report.create",
        details={"name": payload.name, "type": payload.report_type},
    )
    await session.commit()
    await session.refresh(r)
    return _ser(r)


@router.get("")
async def list_scheduled_reports(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    result = await session.execute(
        select(ScheduledReport)
        .where(ScheduledReport.workspace_id == auth.workspace_id)
        .order_by(ScheduledReport.created_at.desc())
    )
    reports = list(result.scalars().all())
    return {"results": [_ser(r) for r in reports], "count": len(reports)}


@router.patch("/{report_id}")
async def update_scheduled_report(
    report_id: str,
    payload: ScheduledReportUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    try:
        parsed_id = uuid_pkg.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")

    result = await session.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == parsed_id,
            ScheduledReport.workspace_id == auth.workspace_id,
        )
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")

    if payload.name is not None:
        r.name = payload.name
    if payload.frequency is not None:
        r.frequency = payload.frequency
        r.next_run_at = _next_run(payload.frequency)
    if payload.recipients is not None:
        r.recipients = payload.recipients
    if payload.config is not None:
        r.config = payload.config
    if payload.enabled is not None:
        r.enabled = payload.enabled

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="scheduled_report.update",
        target_uuid=str(r.id),
        details={"name": r.name},
    )
    await session.commit()
    await session.refresh(r)
    return _ser(r)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_scheduled_report(
    report_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> None:
    try:
        parsed_id = uuid_pkg.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")

    result = await session.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == parsed_id,
            ScheduledReport.workspace_id == auth.workspace_id,
        )
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="scheduled_report.delete",
        target_uuid=str(r.id),
        details={"name": r.name},
    )
    await session.delete(r)
    await session.commit()


@router.post("/{report_id}/run")
async def run_scheduled_report_now(
    report_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Manually trigger a scheduled report immediately."""
    try:
        parsed_id = uuid_pkg.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")

    result = await session.execute(
        select(ScheduledReport).where(
            ScheduledReport.id == parsed_id,
            ScheduledReport.workspace_id == auth.workspace_id,
        )
    )
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found.")

    session_factory = get_session_factory(request)

    from app.services.report_scheduler import run_report_now
    result_data = await run_report_now(session_factory, report_id)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="scheduled_report.run",
        details={"report_id": report_id, "name": r.name},
    )
    await session.commit()

    return result_data
