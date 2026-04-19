from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import SearchRequest
from app.services.insights_service import get_insights_dashboard_payload


router = APIRouter(tags=["insights"])


@router.post("/insights/dashboard")
async def get_insights_dashboard(
    payload: SearchRequest,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
) -> dict[str, object]:
    ensure_workspace_scope(auth, payload.workspace_id)

    return await get_insights_dashboard_payload(
        session=session,
        search=payload,
        workspace_id=auth.workspace_id,
    )
