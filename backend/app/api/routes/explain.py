from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import ExplainRequest, ExplainResponse
from app.services.explanation_service import (
    EXPLANATION_SYSTEM_PROMPT,
    generate_plain_english_explanation,
)
from app.services.provenance_service import get_provenance_by_uuid, serialize_provenance_record


router = APIRouter(tags=["explain"])


@router.post("/explain", response_model=ExplainResponse)
async def explain_provenance(
    payload: ExplainRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth_context),
) -> ExplainResponse:
    ensure_workspace_scope(auth, payload.workspace_id)

    settings = request.app.state.settings

    record_data = payload.record
    resolved_uuid = (payload.uuid or "").strip() or None

    if record_data is None:
        if not resolved_uuid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide either uuid or record payload for /explain.",
            )

        record = await get_provenance_by_uuid(
            session=session,
            record_uuid=resolved_uuid,
            workspace_id=auth.workspace_id,
        )

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provenance record not found for this workspace.",
            )

        record_data = serialize_provenance_record(record)
        resolved_uuid = str(record.uuid)
    else:
        record_workspace = (
            record_data.get("workspaceId")
            or record_data.get("workspace_id")
            or record_data.get("workspace")
        )

        if isinstance(record_workspace, str) and record_workspace and record_workspace != auth.workspace_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Workspace scope mismatch for provided record.",
            )

        if not resolved_uuid:
            inferred_uuid = record_data.get("uuid") or record_data.get("id")
            resolved_uuid = str(inferred_uuid) if inferred_uuid else None

    explanation, model_name, source = await generate_plain_english_explanation(
        record_data,
        settings,
    )

    return ExplainResponse(
        explanation=explanation,
        model=model_name,
        source=source,
        uuid=resolved_uuid,
    )


@router.get("/explain/system-prompt")
async def get_explain_system_prompt(
    auth: AuthContext = Depends(get_current_auth_context),
) -> dict[str, str]:
    # Endpoint is authenticated and workspace-scoped by token to avoid prompt leakage.
    _ = auth.workspace_id

    return {
        "systemPrompt": EXPLANATION_SYSTEM_PROMPT,
    }
