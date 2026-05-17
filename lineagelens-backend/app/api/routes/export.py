from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.models import ProvenanceRecord, UserAccount
from app.db.session import get_db_session
from app.services.export_service import ExportJob, cleanup_old_jobs, new_job, run_export_job
from app.services.provenance_service import build_workspace_record_filters, serialize_provenance_record
from app.schemas.provenance import SearchRequest


router = APIRouter(tags=["export"])
logger = logging.getLogger(__name__)

MAX_EXPORT_ROWS = 10_000

_background_tasks: set[asyncio.Task] = set()


def _pick(record: dict, *keys: str) -> str:
    for k in keys:
        v = record.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _prompt_summary(prompt_messages: object) -> str:
    if not prompt_messages:
        return ""
    if isinstance(prompt_messages, str):
        return prompt_messages[:500]
    if isinstance(prompt_messages, list):
        parts = []
        for msg in prompt_messages[:3]:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    parts.append(content[:200])
        return " | ".join(parts)[:500]
    return str(prompt_messages)[:500]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _filter_records_by_developer(records: list[dict], developer: str) -> list[dict]:
    dev_lower = developer.lower()
    filtered = []
    for r in records:
        snap = r.get("contextSnapshot") or {}
        dev_fields = [str(snap.get("gitUser", "") or ""), str(snap.get("username", "") or "")]
        if any(dev_lower in f.lower() for f in dev_fields):
            filtered.append(r)
    return filtered


def _build_audit_csv_row(r: dict) -> list:
    snap = r.get("contextSnapshot") or {}
    event = r.get("normalizedEvent") or {}
    source = event.get("source") or {}
    diff_block = event.get("diff") or {}
    model_block = event.get("model") or {}
    capture_block = event.get("capture") or {}
    model_val = _pick(r, "modelName") or _pick(model_block, "name") or ""
    risk_block = r.get("riskAssessment") or {}
    risk_level = str(risk_block.get("level", "")) if isinstance(risk_block, dict) else ""
    return [
        _pick(r, "uuid"),
        _pick(r, "timestampIso"),
        _pick(r, "filePath"),
        model_val,
        snap.get("gitUser") or snap.get("username") or "",
        snap.get("gitBranch", ""),
        _pick(source, "toolName"),
        _pick(source, "adapterName"),
        str(diff_block.get("netAddedLines") or r.get("netAddedLines") or ""),
        _prompt_summary(r.get("promptMessages")),
        str(r.get("insertedCode") or "")[:300],
        risk_level,
        _pick(capture_block, "promptStatus"),
    ]


@router.get("/export/audit", response_model=None)
async def export_audit_report(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    date_from: Annotated[str | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[str | None, Query(alias="dateTo")] = None,
    developer: Annotated[str | None, Query()] = None,
    file_path: Annotated[str | None, Query(alias="filePath")] = None,
    format: Annotated[str, Query()] = "csv",
) -> StreamingResponse | JSONResponse:
    try:
        caller_uuid = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject.")
    role_result = await session.execute(
        select(UserAccount.role).where(UserAccount.id == caller_uuid)
    )
    current_role = role_result.scalar_one_or_none()
    if current_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Export requires admin role.",
        )

    search = SearchRequest(
        workspace_id=auth.workspace_id,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        file_path=file_path,
    )

    stmt = (
        select(ProvenanceRecord)
        .where(and_(*build_workspace_record_filters(search, auth.workspace_id)))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
        .limit(MAX_EXPORT_ROWS)
    )

    result = await session.execute(stmt)
    rows = result.scalars().all()
    records = [serialize_provenance_record(row) for row in rows]

    if developer:
        records = _filter_records_by_developer(records, developer)

    record_count = len(records)
    fmt = format.strip().lower()

    logger.info(
        "AUDIT_EXPORT workspace=%s user=%s record_count=%d format=%s",
        auth.workspace_id,
        str(auth.subject),
        record_count,
        fmt,
    )

    audit_action = "export.json" if fmt == "json" else "export.audit"
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action=audit_action,
        details={"record_count": record_count, "format": fmt, "date_from": date_from, "date_to": date_to},
    )
    await session.flush()

    if fmt == "json":
        return JSONResponse(
            content={"results": records, "count": record_count},
            headers={"X-Record-Count": str(record_count)},
        )

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow([
        "uuid", "timestamp", "file_path", "model", "developer", "git_branch",
        "tool", "adapter", "net_added_lines", "prompt_summary",
        "inserted_code_preview", "risk_level", "prompt_capture_status",
    ])
    for r in records:
        writer.writerow(_build_audit_csv_row(r))

    content = output.getvalue()
    filename = f"lineagelens-audit-{auth.workspace_id[:8]}.csv"

    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Record-Count": str(record_count),
        },
    )


class AsyncExportRequest(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    format: str = Field(default="json")  # json, csv, parquet
    date_from: str | None = Field(default=None, alias="dateFrom")
    date_to: str | None = Field(default=None, alias="dateTo")
    model_name: str | None = Field(default=None, alias="modelName")
    limit: int = Field(default=1000, ge=1, le=10000)

    model_config = ConfigDict(populate_by_name=True)


@router.post("/export/async", status_code=status.HTTP_202_ACCEPTED)
async def start_async_export(
    payload: AsyncExportRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Start a background export job. Returns job_id for polling."""
    ensure_workspace_scope(auth, payload.workspace_id)

    fmt = payload.format.strip().lower()
    if fmt not in {"json", "csv", "parquet"}:
        raise HTTPException(status_code=400, detail="format must be 'json', 'csv', or 'parquet'.")

    # Fetch records now (before background task)
    from datetime import timezone

    filters = [ProvenanceRecord.workspace_id == auth.workspace_id]
    if payload.date_from:
        try:
            filters.append(
                ProvenanceRecord.timestamp_iso
                >= datetime.fromisoformat(payload.date_from).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
    if payload.date_to:
        try:
            filters.append(
                ProvenanceRecord.timestamp_iso
                <= datetime.fromisoformat(payload.date_to).replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass
    if payload.model_name:
        filters.append(ProvenanceRecord.model_name == payload.model_name)

    result = await session.execute(
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(ProvenanceRecord.timestamp_iso.desc())
        .limit(payload.limit)
    )
    records = [serialize_provenance_record(r) for r in result.scalars().all()]

    # Get or create jobs store on app.state
    jobs_store: dict = getattr(request.app.state, "export_jobs", None)
    if jobs_store is None:
        request.app.state.export_jobs = {}
        jobs_store = request.app.state.export_jobs

    # Cleanup old jobs opportunistically
    cleanup_old_jobs(jobs_store)

    job = new_job()
    job_key = f"{auth.workspace_id}:{job.job_id}"
    jobs_store[job_key] = job

    # Fire and forget — keep a strong reference so GC cannot collect the task early.
    task = asyncio.create_task(run_export_job(job, records, fmt, jobs_store, job_key))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "jobId": job.job_id,
        "status": job.status,
        "format": fmt,
        "recordCount": len(records),
    }


@router.get("/export/jobs/{job_id}")
async def get_export_job_status(
    job_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Poll export job status."""
    jobs_store: dict = getattr(request.app.state, "export_jobs", {})
    job = jobs_store.get(f"{auth.workspace_id}:{job_id}")
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")

    return {
        "jobId": job.job_id,
        "status": job.status,
        "filename": job.filename,
        "error": job.error,
        "createdAt": job.created_at,
        "completedAt": job.completed_at,
    }


@router.get("/export/jobs/{job_id}/download")
async def download_export_job(
    job_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
):
    """Download a completed export job result."""
    from fastapi.responses import Response

    jobs_store: dict = getattr(request.app.state, "export_jobs", {})
    job = jobs_store.get(f"{auth.workspace_id}:{job_id}")
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    if job.status in {"pending", "running"}:
        raise HTTPException(status_code=202, detail="Export job is still in progress.")
    if job.status == "failed":
        raise HTTPException(status_code=500, detail=f"Export job failed: {job.error}")
    if not job.result_bytes:
        raise HTTPException(status_code=500, detail="Export job produced no output.")

    return Response(
        content=job.result_bytes,
        media_type=job.result_content_type,
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )
