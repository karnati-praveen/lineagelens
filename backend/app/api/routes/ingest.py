from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import IngestRequest, IngestResponse
from app.services.provenance_service import ingest_provenance_event


router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
async def ingest_provenance(
    payload: IngestRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
) -> IngestResponse:
    ensure_workspace_scope(auth, payload.workspace_id)

    neo4j_service = request.app.state.neo4j_service
    settings = request.app.state.settings

    try:
        record = await ingest_provenance_event(
            session=session,
            payload=payload,
            auth=auth,
            settings=settings,
            neo4j_service=neo4j_service,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest provenance payload: {error}",
        ) from error

    return IngestResponse(
        uuid=str(record.uuid),
        workspaceId=record.workspace_id,
        lineageNodeId=record.lineage_node_id,
        stored=True,
    )
