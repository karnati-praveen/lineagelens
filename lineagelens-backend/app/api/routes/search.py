from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
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

    rows, warnings, total, next_cursor = await search_provenance_records(
        session=session,
        search=payload,
        workspace_id=auth.workspace_id,
        settings=settings,
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

    return SearchResponse(
        results=items,
        count=len(items),
        total=total,
        offset=payload.offset,
        next_cursor=next_cursor,
        warnings=warnings,
    )
