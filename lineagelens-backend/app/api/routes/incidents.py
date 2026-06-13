from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.encryption import decrypt_field, encrypt_field
from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, get_current_auth_context, require_admin
from app.db.models import Incident, IncidentIntegration, ProvenanceRecord, ProvenanceTag
from app.db.session import get_db_session

router = APIRouter(
    prefix="/incidents",
    tags=["incidents"],
    dependencies=[Depends(require_non_solo)],
)
logger = logging.getLogger(__name__)


# ─── Schemas ──────────────────────────────────────────────────────────────────

class IncidentCreate(BaseModel):
    title: str = Field(max_length=256)
    description: str | None = None
    started_at: datetime = Field(alias="startedAt")
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")
    affected_files: list[str] = Field(alias="affectedFiles")
    external_source: str | None = Field(default=None, alias="externalSource")
    external_ref: str | None = Field(default=None, alias="externalRef")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("affected_files")
    @classmethod
    def _validate_files(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("affectedFiles must be a non-empty list of strings")
        if not all(isinstance(f, str) and f.strip() for f in v):
            raise ValueError("affectedFiles must contain non-empty strings")
        return v


class IncidentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")
    affected_files: list[str] | None = Field(default=None, alias="affectedFiles")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("affected_files")
    @classmethod
    def _validate_files(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and not v:
            raise ValueError("affectedFiles must be a non-empty list if provided")
        return v


class WebhookConfigRequest(BaseModel):
    webhook_secret: str = Field(alias="webhookSecret", min_length=8)

    model_config = ConfigDict(populate_by_name=True)


def _incident_to_dict(inc: Incident) -> dict:
    return {
        "uuid": str(inc.uuid),
        "workspaceId": inc.workspace_id,
        "title": inc.title,
        "description": inc.description,
        "startedAt": inc.started_at.isoformat(),
        "resolvedAt": inc.resolved_at.isoformat() if inc.resolved_at else None,
        "affectedFiles": inc.affected_files,
        "externalSource": inc.external_source,
        "externalRef": inc.external_ref,
        "createdBy": str(inc.created_by) if inc.created_by else None,
        "createdAt": inc.created_at.isoformat(),
        "updatedAt": inc.updated_at.isoformat(),
    }


# ─── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_incident(
    payload: IncidentCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    try:
        creator_uuid = uuid_pkg.UUID(auth.subject) if auth.subject else None
    except ValueError:
        creator_uuid = None

    inc = Incident(
        uuid=uuid_pkg.uuid4(),
        workspace_id=auth.workspace_id,
        title=payload.title,
        description=payload.description,
        started_at=payload.started_at,
        resolved_at=payload.resolved_at,
        affected_files=payload.affected_files,
        external_source=payload.external_source,
        external_ref=payload.external_ref,
        created_by=creator_uuid,
    )
    session.add(inc)
    await session.flush()
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="incident.create",
        target_uuid=str(inc.uuid),
        details={"title": inc.title, "affected_files_count": len(inc.affected_files)},
    )
    await session.commit()
    await session.refresh(inc)
    return _incident_to_dict(inc)


@router.get("")
async def list_incidents(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    stmt = (
        select(Incident)
        .where(Incident.workspace_id == auth.workspace_id)
        .order_by(desc(Incident.started_at))
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    items = result.scalars().all()
    return {
        "items": [_incident_to_dict(i) for i in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{incident_uuid}/provenance")
async def get_incident_provenance(
    incident_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    window_days: int = Query(default=90, ge=1, le=3650, alias="windowDays"),
) -> dict:
    inc = await _get_incident_or_404(session, incident_uuid, auth.workspace_id)

    affected = inc.affected_files or []
    if not affected:
        return {"incident": _incident_to_dict(inc), "windowDays": window_days, "items": [], "total": 0}

    window_start = inc.started_at - timedelta(days=window_days)
    file_clause = _build_file_clause(affected)

    stmt = (
        select(ProvenanceRecord)
        .where(
            ProvenanceRecord.workspace_id == auth.workspace_id,
            file_clause,
            ProvenanceRecord.timestamp_iso <= inc.started_at,
            ProvenanceRecord.timestamp_iso >= window_start,
        )
        .order_by(
            # risk_score DESC nulls last: non-null first (case=0), null last (case=1)
            case((ProvenanceRecord.risk_score.is_(None), 1), else_=0),
            desc(ProvenanceRecord.risk_score),
            desc(ProvenanceRecord.timestamp_iso),
        )
    )
    result = await session.execute(stmt)
    records = result.scalars().all()

    record_uuid_strs = [str(r.uuid) for r in records]
    tags_by_record: dict[str, list[str]] = {uid: [] for uid in record_uuid_strs}
    if record_uuid_strs:
        tags_result = await session.execute(
            select(ProvenanceTag).where(ProvenanceTag.record_uuid.in_(record_uuid_strs))
        )
        for tag_row in tags_result.scalars().all():
            if tag_row.record_uuid in tags_by_record:
                tags_by_record[tag_row.record_uuid].append(tag_row.tag)

    items = [
        {
            "uuid": str(r.uuid),
            "filePath": r.file_path,
            "timestampIso": r.timestamp_iso.isoformat(),
            "modelName": r.model_name,
            "riskScore": r.risk_score,
            "confidenceBreakdown": r.confidence_breakdown,
            "tags": tags_by_record.get(str(r.uuid), []),
            "insertedCodePreview": (r.inserted_code or "")[:200],
        }
        for r in records
    ]

    return {
        "incident": _incident_to_dict(inc),
        "windowDays": window_days,
        "items": items,
        "total": len(items),
    }


@router.get("/{incident_uuid}")
async def get_incident(
    incident_uuid: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    inc = await _get_incident_or_404(session, incident_uuid, auth.workspace_id)
    return _incident_to_dict(inc)


@router.patch("/{incident_uuid}")
async def patch_incident(
    incident_uuid: str,
    payload: IncidentPatch,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    inc = await _get_incident_or_404(session, incident_uuid, auth.workspace_id)
    if payload.title is not None:
        inc.title = payload.title
    if payload.resolved_at is not None:
        inc.resolved_at = payload.resolved_at
    if payload.affected_files is not None:
        inc.affected_files = payload.affected_files
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="incident.update",
        target_uuid=str(inc.uuid),
        details={"resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None},
    )
    await session.commit()
    await session.refresh(inc)
    return _incident_to_dict(inc)


# ─── Webhook config ───────────────────────────────────────────────────────────

@router.put("/webhook/config")
async def configure_webhook(
    payload: WebhookConfigRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    result = await session.execute(
        select(IncidentIntegration).where(IncidentIntegration.workspace_id == auth.workspace_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = IncidentIntegration(workspace_id=auth.workspace_id)
        session.add(config)
    config.webhook_secret = encrypt_field(payload.webhook_secret)
    await session.commit()
    return {"configured": True}


# ─── Webhook intake ───────────────────────────────────────────────────────────

@router.post("/webhook")
async def receive_incident_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    workspace_id = request.headers.get("X-LineageLens-Workspace")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="X-LineageLens-Workspace header required.")

    result = await session.execute(
        select(IncidentIntegration).where(IncidentIntegration.workspace_id == workspace_id)
    )
    config = result.scalar_one_or_none()
    if config is None or not (config.webhook_secret or "").strip():
        raise HTTPException(status_code=403, detail="Incident webhooks are not configured for this workspace.")

    body = await request.body()
    raw_secret = decrypt_field(config.webhook_secret or "")
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    expected_sig = "sha256=" + hmac.new(raw_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_header, expected_sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    inc_data = _try_parse_sentry(event) or _parse_generic(event)
    if inc_data is None:
        raise HTTPException(status_code=422, detail="Cannot extract incident fields from payload.")

    inc = Incident(
        uuid=uuid_pkg.uuid4(),
        workspace_id=workspace_id,
        title=inc_data["title"],
        description=inc_data.get("description"),
        started_at=inc_data["started_at"],
        affected_files=inc_data.get("files") or [],
        external_source=inc_data.get("source"),
        external_ref=inc_data.get("external_ref") or None,
    )
    session.add(inc)
    await session.flush()
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=None,
        action="incident.webhook.intake",
        target_uuid=str(inc.uuid),
        details={"source": inc.external_source, "title": inc.title},
    )
    await session.commit()
    return {"received": True, "incidentUuid": str(inc.uuid)}


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _get_incident_or_404(session: AsyncSession, uuid_str: str, workspace_id: str) -> Incident:
    try:
        parsed = uuid_pkg.UUID(uuid_str)
    except ValueError:
        raise HTTPException(status_code=404, detail="Incident not found.")
    result = await session.execute(select(Incident).where(Incident.uuid == parsed))
    inc = result.scalar_one_or_none()
    if inc is None or inc.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return inc


def _normalize(p: str) -> str:
    return p.replace("\\", "/")


def _basename(p: str) -> str:
    norm = _normalize(p)
    return norm.rsplit("/", 1)[-1] if "/" in norm else norm


def _build_file_clause(affected_files: list[str]):
    conditions = []
    for f in affected_files:
        norm = _normalize(f)
        bn = _basename(f)
        conditions.append(ProvenanceRecord.file_path == f)
        if norm != f:
            conditions.append(ProvenanceRecord.file_path == norm)
        # Basename match: normalize backslashes then check suffix /basename
        conditions.append(func.replace(ProvenanceRecord.file_path, "\\", "/").like(f"%/{bn}"))
        # Plain basename (record stored without any directory prefix)
        conditions.append(ProvenanceRecord.file_path == bn)
    return or_(*conditions)


def _try_parse_sentry(payload: dict) -> dict | None:
    event = payload.get("event")
    if not isinstance(event, dict):
        return None

    title = event.get("title") or payload.get("message") or payload.get("culprit")
    if not title:
        return None

    started_at: datetime | None = None
    ts = event.get("timestamp")
    if ts is not None:
        try:
            started_at = datetime.fromtimestamp(float(ts), tz=UTC)
        except (ValueError, TypeError, OSError):
            pass
    if not started_at:
        for key in ("datetime", "received"):
            dt_str = event.get(key)
            if dt_str:
                try:
                    started_at = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
                    break
                except ValueError:
                    continue
    if not started_at:
        started_at = datetime.now(UTC)

    files: list[str] = []
    seen: set[str] = set()
    for entry in (event.get("entries") or []):
        if not isinstance(entry, dict):
            continue
        data = entry.get("data") or {}
        for exc_val in (data.get("values") or []):
            if not isinstance(exc_val, dict):
                continue
            st = exc_val.get("stacktrace") or {}
            for frame in (st.get("frames") or []):
                if not isinstance(frame, dict):
                    continue
                fn = frame.get("filename") or frame.get("abs_path") or ""
                if fn and fn not in seen:
                    seen.add(fn)
                    files.append(str(fn))

    return {
        "title": str(title),
        "started_at": started_at,
        "files": files,
        "source": "sentry",
        "external_ref": str(payload.get("id") or payload.get("url") or ""),
    }


def _parse_generic(payload: dict) -> dict | None:
    title = payload.get("title")
    if not title:
        return None

    started_at: datetime | None = None
    for key in ("startedAt", "started_at", "timestamp"):
        val = payload.get(key)
        if val:
            try:
                if isinstance(val, (int, float)):
                    started_at = datetime.fromtimestamp(float(val), tz=UTC)
                else:
                    started_at = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                break
            except (ValueError, TypeError, OSError):
                continue
    if not started_at:
        started_at = datetime.now(UTC)

    files = payload.get("files") or []
    if not isinstance(files, list):
        files = []

    return {
        "title": str(title),
        "started_at": started_at,
        "files": [str(f) for f in files if isinstance(f, str)],
        "source": payload.get("source"),
        "external_ref": str(payload.get("externalRef") or payload.get("external_ref") or ""),
        "description": payload.get("description"),
    }
