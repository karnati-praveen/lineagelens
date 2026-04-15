from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import ProvenanceResponse
from app.services.provenance_service import get_provenance_by_uuid, serialize_provenance_record


router = APIRouter(tags=["provenance"])


@router.get("/provenance/{record_uuid}", response_model=ProvenanceResponse)
async def get_provenance_record(
    record_uuid: str,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
) -> ProvenanceResponse:
    record = await get_provenance_by_uuid(
        session=session,
        record_uuid=record_uuid,
        workspace_id=auth.workspace_id,
    )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )

    return ProvenanceResponse(uuid=str(record.uuid), record=serialize_provenance_record(record))
