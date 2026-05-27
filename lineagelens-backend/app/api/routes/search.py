from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.config import Settings
from app.core.mode_guard import require_non_solo
from app.core.security import (
    AuthContext,
    build_record_visibility_clause,
    ensure_workspace_scope,
    get_current_auth_context,
    get_verified_user_role,
)
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.schemas.provenance import SearchRequest, SearchResponse, SearchResultItem
from app.services.provenance_service import search_provenance_records, serialize_provenance_record


router = APIRouter(tags=["search"])


@router.post("/search", dependencies=[Depends(require_non_solo)])
async def search_provenance(
    payload: SearchRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> SearchResponse:
    ensure_workspace_scope(auth, payload.workspace_id)

    settings: Settings = request.app.state.settings
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="search.execute",
        details={"query": payload.query or payload.keywords or "", "offset": payload.offset},
    )
    await session.commit()

    rows, warnings, total, next_cursor = await search_provenance_records(
        session=session,
        search=payload,
        workspace_id=auth.workspace_id,
        settings=settings,
        access_filters=[access_clause],
    )

    items: list[SearchResultItem] = []
    for record, score in rows:
        serialized = serialize_provenance_record(record, score=score)
        timestamp_iso = serialized.get("timestampIso") or record.timestamp_iso.isoformat()
        file_path = serialized.get("filePath") or record.file_path
        model_name = serialized.get("modelName") or record.model_name
        snippet = (
            serialized.get("insertedCode")
            or serialized.get("normalizedEvent", {}).get("diff", {}).get("insertedText")
            or record.inserted_code
            or ""
        )[:700]
        items.append(
            SearchResultItem(
                uuid=str(serialized.get("uuid") or record.uuid),
                score=serialized.get("score", score),
                model=model_name,
                timestampIso=timestamp_iso,
                filePath=file_path,
                snippet=snippet,
                record=serialized,
            )
        )

    effective_limit = payload.top_k or payload.limit or settings.search_default_limit

    return SearchResponse(
        results=items,
        count=len(items),
        total=total,
        offset=payload.offset,
        limit=effective_limit,
        has_more=(total is not None and (payload.offset + len(items)) < total),
        next_cursor=next_cursor,
        warnings=warnings,
    )


@router.get("/search/facets", dependencies=[Depends(require_non_solo)])
async def get_search_facets(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return aggregated facet counts for filter UI: models, risk levels, file extensions, capture status."""
    ensure_workspace_scope(auth, auth.workspace_id)
    role = await get_verified_user_role(session, auth)
    access_clause = build_record_visibility_clause(
        ProvenanceRecord.uuid,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        is_admin=role == "admin",
    )
    ws = and_(ProvenanceRecord.workspace_id == auth.workspace_id, access_clause)

    # Model name facets (top 20)
    model_rows = await session.execute(
        select(ProvenanceRecord.model_name, func.count(ProvenanceRecord.id).label("cnt"))
        .where(ws, ProvenanceRecord.model_name.isnot(None))
        .group_by(ProvenanceRecord.model_name)
        .order_by(func.count(ProvenanceRecord.id).desc())
        .limit(20)
    )
    model_facets = [{"value": r.model_name, "count": r.cnt} for r in model_rows]

    # Risk level facets (bucketed from risk_score)
    risk_rows = await session.execute(
        select(
            case(
                (ProvenanceRecord.risk_score.is_(None), "unknown"),
                (ProvenanceRecord.risk_score >= 80, "critical"),
                (ProvenanceRecord.risk_score >= 60, "high"),
                (ProvenanceRecord.risk_score >= 30, "medium"),
                else_="low",
            ).label("risk_level"),
            func.count(ProvenanceRecord.id).label("cnt"),
        )
        .where(ws)
        .group_by("risk_level")
        .order_by(func.count(ProvenanceRecord.id).desc())
    )
    risk_facets = [{"value": r.risk_level, "count": r.cnt} for r in risk_rows]

    # Capture status facets
    capture_rows = await session.execute(
        select(
            case(
                (ProvenanceRecord.prompt_messages.isnot(None), "captured"),
                else_="uncaptured",
            ).label("capture_status"),
            func.count(ProvenanceRecord.id).label("cnt"),
        )
        .where(ws)
        .group_by("capture_status")
    )
    capture_facets = [{"value": r.capture_status, "count": r.cnt} for r in capture_rows]

    # File extension: fetch grouped file paths and process in Python to avoid DB-specific regex
    fp_rows = await session.execute(
        select(ProvenanceRecord.file_path, func.count(ProvenanceRecord.id).label("cnt"))
        .where(ws)
        .group_by(ProvenanceRecord.file_path)
        .limit(2000)
    )
    ext_counts: dict[str, int] = {}
    for row in fp_rows:
        if row.file_path and "." in row.file_path:
            base = row.file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if "." in base:
                ext = "." + base.rsplit(".", 1)[-1].lower()
                ext_counts[ext] = ext_counts.get(ext, 0) + row.cnt
    ext_facets = sorted(
        [{"value": k, "count": v} for k, v in ext_counts.items()],
        key=lambda x: -x["count"],
    )[:20]

    return {
        "model_name": model_facets,
        "risk_level": risk_facets,
        "capture_status": capture_facets,
        "file_extension": ext_facets,
    }
