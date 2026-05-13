import csv
import io
from datetime import datetime
from typing import Annotated
from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context
from app.db.models import ProvenanceRecord, UserAccount
from app.db.session import get_db_session
from app.services.provenance_service import build_workspace_record_filters, serialize_provenance_record
from app.schemas.provenance import SearchRequest


router = APIRouter(tags=["export"])

MAX_EXPORT_ROWS = 10_000


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


@router.get("/export/audit")
async def export_audit_report(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    date_from: Annotated[str | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[str | None, Query(alias="dateTo")] = None,
    developer: Annotated[str | None, Query()] = None,
    file_path: Annotated[str | None, Query(alias="filePath")] = None,
) -> StreamingResponse:
    role_result = await session.execute(
        select(UserAccount.role).where(UserAccount.id == PyUUID(auth.subject))
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
            "X-Record-Count": str(len(records)),
        },
    )
