from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProvenanceRecord, ScheduledReport

logger = logging.getLogger(__name__)

_SCHEDULER_TASK: asyncio.Task | None = None


def start_scheduler(session_factory) -> None:
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK is not None and not _SCHEDULER_TASK.done():
        return
    _SCHEDULER_TASK = asyncio.create_task(_scheduler_loop(session_factory))
    logger.info("Report scheduler started.")


async def stop_scheduler() -> None:
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK and not _SCHEDULER_TASK.done():
        _SCHEDULER_TASK.cancel()
        await asyncio.gather(_SCHEDULER_TASK, return_exceptions=True)
    logger.info("Report scheduler stopped.")


async def _scheduler_loop(session_factory) -> None:
    while True:
        try:
            await asyncio.sleep(300)  # poll every 5 minutes
            await _run_due_reports(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Scheduler loop error: %s", exc)


async def _run_due_reports(session_factory) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        result = await session.execute(
            select(ScheduledReport).where(
                ScheduledReport.enabled.is_(True),
                ScheduledReport.next_run_at.isnot(None),
                ScheduledReport.next_run_at <= now,
            )
        )
        due = list(result.scalars().all())

    for report in due:
        try:
            await _execute_report(session_factory, report.id)
        except Exception as exc:
            logger.warning("Failed to execute scheduled report %s: %s", report.id, exc)


async def _execute_report(session_factory, report_id) -> None:
    async with session_factory() as session:
        result = await session.execute(
            select(ScheduledReport).where(ScheduledReport.id == report_id)
        )
        r = result.scalar_one_or_none()
        if r is None or not r.enabled:
            return

        now = datetime.now(UTC)
        # Always advance next_run_at first so a failure doesn't keep the report
        # selected as "due" on every subsequent scheduler tick.
        r.last_run_at = now
        r.next_run_at = _next_run(r.frequency, now)
        await session.commit()

        body = await _build_report_body(session, r)

        if r.recipients:
            await _dispatch_email(r, body)

        logger.info("Executed scheduled report %s (type=%s)", r.id, r.report_type)


async def _build_report_body(session: AsyncSession, report: ScheduledReport) -> str:
    now = datetime.now(UTC)
    try:
        days = max(1, int(report.config.get("date_range_days", 7)))
    except (TypeError, ValueError):
        days = 7
    since = now - timedelta(days=days)
    ws = report.workspace_id

    total_result = await session.execute(
        select(func.count(ProvenanceRecord.id)).where(
            ProvenanceRecord.workspace_id == ws,
            ProvenanceRecord.timestamp_iso >= since,
        )
    )
    total = total_result.scalar_one() or 0

    high_risk_result = await session.execute(
        select(func.count(ProvenanceRecord.id)).where(
            ProvenanceRecord.workspace_id == ws,
            ProvenanceRecord.timestamp_iso >= since,
            ProvenanceRecord.risk_score >= 70,
        )
    )
    high_risk = high_risk_result.scalar_one() or 0

    avg_risk_result = await session.execute(
        select(func.avg(ProvenanceRecord.risk_score)).where(
            ProvenanceRecord.workspace_id == ws,
            ProvenanceRecord.timestamp_iso >= since,
            ProvenanceRecord.risk_score.isnot(None),
        )
    )
    avg_risk = avg_risk_result.scalar_one()
    avg_risk_str = f"{avg_risk:.1f}" if avg_risk is not None else "N/A"

    pct_str = f"{round(high_risk / total * 100, 1)}%" if total > 0 else "N/A"

    lines = [
        f"LineageLens {report.report_type.replace('_', ' ').title()} Report",
        f"Period: last {days} days (as of {now.strftime('%Y-%m-%d %H:%M UTC')})",
        f"Workspace: {ws}",
        "",
        f"Total AI insertions:       {total}",
        f"High-risk insertions (≥70): {high_risk}",
        f"High-risk rate:            {pct_str}",
        f"Average risk score:        {avg_risk_str}",
        "",
        "---",
        "Sent by LineageLens scheduled report. To unsubscribe, visit your dashboard.",
    ]
    return "\n".join(lines)


async def _dispatch_email(report: ScheduledReport, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        logger.warning("SMTP_HOST not configured; skipping scheduled report email dispatch.")
        return

    import smtplib
    from email.mime.text import MIMEText

    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM", smtp_user or "noreply@lineagelens.io")
    subject = f"LineageLens {report.report_type.replace('_', ' ').title()} Report"

    def _send() -> None:
        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(report.recipients)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, report.recipients, msg.as_string())

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send)


def _next_run(frequency: str, from_dt: datetime) -> datetime:
    if frequency == "daily":
        return from_dt + timedelta(days=1)
    if frequency == "monthly":
        return from_dt + timedelta(days=30)
    return from_dt + timedelta(weeks=1)


async def run_report_now(session_factory, report_id: str) -> dict:
    """Trigger a specific report immediately and return run metadata."""
    import uuid as uuid_pkg
    parsed_id = uuid_pkg.UUID(report_id)

    async with session_factory() as session:
        result = await session.execute(
            select(ScheduledReport).where(ScheduledReport.id == parsed_id)
        )
        report = result.scalar_one_or_none()
        if report is None:
            raise ValueError(f"Scheduled report {report_id} not found.")

        body = await _build_report_body(session, report)

        if report.recipients:
            await _dispatch_email(report, body)

        now = datetime.now(UTC)
        report.last_run_at = now
        report.next_run_at = _next_run(report.frequency, now)
        await session.commit()

    return {
        "runAt": now.isoformat(),
        "dispatchedTo": len(report.recipients) if report.recipients else 0,
        "reportType": report.report_type,
    }
