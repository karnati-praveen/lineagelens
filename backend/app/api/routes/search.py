from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import SearchRequest, SearchResponse, SearchResultItem
from app.services.provenance_service import search_provenance_records, serialize_provenance_record


router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_provenance(
    payload: SearchRequest,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
) -> SearchResponse:
    ensure_workspace_scope(auth, payload.workspace_id)

    settings = get_settings()

    rows, warnings = await search_provenance_records(
        session=session,
        search=payload,
        workspace_id=auth.workspace_id,
        settings=settings,
    )

    items: list[SearchResultItem] = []
    for record, score in rows:
        serialized = serialize_provenance_record(record, score=score)
        items.append(
            SearchResultItem(
                uuid=str(record.uuid),
                score=score,
                model=record.model_name,
                timestampIso=record.timestamp_iso.isoformat(),
                filePath=record.file_path,
                snippet=(record.inserted_code or "")[:700],
                record=serialized,
            )
        )

    return SearchResponse(results=items, count=len(items), warnings=warnings)
