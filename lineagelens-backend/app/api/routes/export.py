from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import UTC, datetime, timezone
from typing import Annotated
from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import (
    AuthContext,
    build_record_visibility_clause,
    ensure_workspace_scope,
    get_current_auth_context,
    get_verified_user_role,
)
from app.db.models import ProvenanceRecord, UserAccount
from app.db.session import get_db_session
from app.schemas.agent_trace import AgentTraceRecord, SCHEMA_VERSION
from app.services.agent_trace_service import record_to_agent_trace
from app.services.export_service import cleanup_old_jobs, deserialize_job, new_job, run_export_job, serialize_job
from app.services.provenance_service import build_workspace_record_filters, serialize_provenance_record
from app.schemas.provenance import SearchRequest


router = APIRouter(tags=["export"])
logger = logging.getLogger(__name__)

MAX_EXPORT_ROWS = 10_000

_background_tasks: set[asyncio.Task] = set()

# Characters that trigger formula execution in spreadsheet applications.
_CSV_FORMULA_PREFIXES = frozenset("=+-@\t\r")


def _safe_csv_value(value: str) -> str:
    """Prefix formula-injection characters so they are treated as plain text.

    Spreadsheets (Excel, LibreOffice Calc, Google Sheets) execute cell values
    starting with ``=``, ``+``, ``-``, or ``@`` as formulas.  Prepending a
    single quote disarms this without altering the visible cell content.
    """
    if value and value[0] in _CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


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
        _safe_csv_value(_pick(r, "uuid")),
        _safe_csv_value(_pick(r, "timestampIso")),
        _safe_csv_value(_pick(r, "filePath")),
        _safe_csv_value(model_val),
        _safe_csv_value(snap.get("gitUser") or snap.get("username") or ""),
        _safe_csv_value(snap.get("gitBranch", "")),
        _safe_csv_value(_pick(source, "toolName")),
        _safe_csv_value(_pick(source, "adapterName")),
        _safe_csv_value(str(diff_block.get("netAddedLines") or r.get("netAddedLines") or "")),
        _safe_csv_value(_prompt_summary(r.get("promptMessages"))),
        _safe_csv_value(str(r.get("insertedCode") or "")[:300]),
        _safe_csv_value(risk_level),
        _safe_csv_value(_pick(capture_block, "promptStatus")),
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

    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=True,
    )

    search = SearchRequest(
        workspace_id=auth.workspace_id,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        file_path=file_path,
    )

    stmt = (
        select(ProvenanceRecord)
        .where(and_(*build_workspace_record_filters(search, auth.workspace_id), access_clause))
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
    await session.commit()

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

    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )

    fmt = payload.format.strip().lower()
    if fmt not in {"json", "csv", "parquet"}:
        raise HTTPException(status_code=400, detail="format must be 'json', 'csv', or 'parquet'.")

    # Fetch records now (before background task)
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
        .where(and_(*filters, access_clause))
        .order_by(ProvenanceRecord.timestamp_iso.desc())
        .limit(payload.limit)
    )
    records = [serialize_provenance_record(r) for r in result.scalars().all()]

    kv_store = request.app.state.kv_store

    # Opportunistically clean up stale in-memory jobs (no-op for Redis)
    await cleanup_old_jobs(kv_store)

    job = new_job()
    job_key = f"{auth.workspace_id}:{job.job_id}"
    await kv_store.set(job_key, serialize_job(job))

    # Fire and forget — keep a strong reference so GC cannot collect the task early.
    task = asyncio.create_task(run_export_job(job, records, fmt, kv_store, job_key))
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
    kv_store = request.app.state.kv_store
    job = deserialize_job(await kv_store.get(f"{auth.workspace_id}:{job_id}"))
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

    kv_store = request.app.state.kv_store
    job = deserialize_job(await kv_store.get(f"{auth.workspace_id}:{job_id}"))
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found.")
    if job.status in {"pending", "running"}:
        raise HTTPException(status_code=409, detail="Export job is still in progress.")
    if job.status == "failed":
        raise HTTPException(status_code=500, detail=f"Export job failed: {job.error}")
    if not job.result_bytes:
        raise HTTPException(status_code=500, detail="Export job produced no output.")

    return Response(
        content=job.result_bytes,
        media_type=job.result_content_type,
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )


# ── Agent Trace export ─────────────────────────────────────────────────────────


@router.get("/export/agent-trace", response_model=None)
async def export_agent_trace(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    date_from: Annotated[str | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[str | None, Query(alias="dateTo")] = None,
    tool_name: Annotated[str | None, Query(alias="toolName")] = None,
    min_confidence: Annotated[float | None, Query(alias="minConfidence")] = None,
    format: Annotated[str, Query()] = "jsonl",
) -> StreamingResponse | JSONResponse:
    """Export agent attribution traces as JSONL (default), JSON, or CSV.

    This is the portable Agent Trace format — import it into another
    LineageLens instance with POST /import/agent-trace.
    """
    try:
        caller_uuid = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject.")

    role_result = await session.execute(
        select(UserAccount.role).where(UserAccount.id == caller_uuid)
    )
    if role_result.scalar_one_or_none() != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent Trace export requires admin role.")

    filters = [ProvenanceRecord.workspace_id == auth.workspace_id]
    if date_from:
        dt = _parse_dt(date_from)
        if dt:
            filters.append(ProvenanceRecord.timestamp_iso >= dt)
    if date_to:
        dt = _parse_dt(date_to)
        if dt:
            filters.append(ProvenanceRecord.timestamp_iso <= dt)

    stmt = (
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
        .limit(MAX_EXPORT_ROWS)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    traces = [record_to_agent_trace(r) for r in rows]

    # Optional post-filter by tool name / confidence (can't do in SQL easily)
    if tool_name:
        tl = tool_name.lower()
        traces = [t for t in traces if (t.tool.name or "").lower() == tl or (t.tool.adapter or "").lower() == tl]
    if min_confidence is not None:
        traces = [t for t in traces if (t.confidence.score or 0.0) >= min_confidence]

    record_count = len(traces)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="export.agent_trace",
        details={"record_count": record_count, "format": format, "tool_name": tool_name},
    )
    await session.commit()

    fmt = format.strip().lower()
    timestamp_tag = datetime.now(tz=UTC).strftime("%Y%m%d")
    ws_slug = auth.workspace_id[:8]

    if fmt == "json":
        data = [t.model_dump(exclude_none=True) for t in traces]
        return JSONResponse(
            content={"version": SCHEMA_VERSION, "count": record_count, "records": data},
            headers={"X-Record-Count": str(record_count)},
        )

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow([
            "id", "timestamp", "file_path", "tool_name", "adapter", "provider",
            "session_id", "operation_type", "session_kind", "model_id",
            "contributor_type", "confidence_score", "confidence_level",
            "start_line", "end_line", "net_added_lines", "inserted_code_preview",
        ])
        for t in traces:
            first_file = t.files[0] if t.files else None
            first_conv = first_file.conversations[0] if (first_file and first_file.conversations) else None
            contributor = first_conv.contributor if first_conv else None
            first_range = first_conv.ranges[0] if (first_conv and first_conv.ranges) else None
            meta = t.metadata or {}
            ll_tool = meta.get("lineagelens.tool") or {}
            ll_conf = meta.get("lineagelens.confidence") or {}
            writer.writerow([
                _safe_csv_value(t.id),
                _safe_csv_value(t.timestamp),
                _safe_csv_value(first_file.path if first_file else ""),
                _safe_csv_value(t.tool.name if t.tool else ""),
                _safe_csv_value(ll_tool.get("adapter") or ""),
                _safe_csv_value(ll_tool.get("provider") or ""),
                _safe_csv_value(ll_tool.get("sessionId") or ""),
                _safe_csv_value(ll_tool.get("operationType") or ""),
                _safe_csv_value(ll_tool.get("sessionKind") or ""),
                _safe_csv_value(contributor.model_id if contributor else ""),
                _safe_csv_value(contributor.type if contributor else ""),
                _safe_csv_value(str(ll_conf.get("score") or "")),
                _safe_csv_value(ll_conf.get("level") or ""),
                _safe_csv_value(str(first_range.start_line if first_range else "")),
                _safe_csv_value(str(first_range.end_line if first_range else "")),
                _safe_csv_value(str(meta.get("lineagelens.netAddedLines") or "")),
                _safe_csv_value(meta.get("lineagelens.insertedCodePreview") or ""),
            ])
        filename = f"lineagelens-agent-trace-{ws_slug}-{timestamp_tag}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Record-Count": str(record_count),
            },
        )

    # Default: JSONL (newline-delimited JSON) — exclude_none so optional spec
    # fields like vcs/tool that are null are omitted, keeping output valid per schema.
    lines = [json.dumps(t.model_dump(exclude_none=True), separators=(",", ":")) for t in traces]
    content = "\n".join(lines) + ("\n" if lines else "")
    filename = f"lineagelens-agent-trace-{ws_slug}-{timestamp_tag}.jsonl"
    return StreamingResponse(
        iter([content]),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Record-Count": str(record_count),
        },
    )
