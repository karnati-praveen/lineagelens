from __future__ import annotations

from typing import Annotated

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


@router.post("/explain")
async def explain_provenance(
    payload: ExplainRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> ExplainResponse:
    ensure_workspace_scope(auth, payload.workspace_id)

    settings = request.app.state.settings
    resolved_uuid = (payload.uuid or "").strip() or None

    if payload.record is None:
        record_data, resolved_uuid = await _fetch_record_by_uuid(session, resolved_uuid, auth.workspace_id)
    else:
        record_data = payload.record
        _assert_record_workspace(record_data, auth.workspace_id)
        if not resolved_uuid:
            inferred = record_data.get("uuid") or record_data.get("id")
            resolved_uuid = str(inferred) if inferred else None

    explanation, model_name, source = await generate_plain_english_explanation(record_data, settings)

    return ExplainResponse(explanation=explanation, model=model_name, source=source, uuid=resolved_uuid)


async def _fetch_record_by_uuid(
    session: AsyncSession,
    record_uuid: str | None,
    workspace_id: str,
) -> tuple[dict, str]:
    if not record_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either uuid or record payload for /explain.",
        )
    record = await get_provenance_by_uuid(session=session, record_uuid=record_uuid, workspace_id=workspace_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )
    return serialize_provenance_record(record), str(record.uuid)


def _assert_record_workspace(record_data: dict, workspace_id: str) -> None:
    record_workspace = (
        record_data.get("workspaceId") or record_data.get("workspace_id") or record_data.get("workspace")
    )
    # Reject if the record declares a *different* workspace.
    if isinstance(record_workspace, str) and record_workspace and record_workspace != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace scope mismatch for provided record.",
        )
    # Also reject if the record has no workspace field at all — a crafted payload
    # without any workspace claim must not bypass scope enforcement.
    if not record_workspace:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Record payload must include a workspace identifier.",
        )


@router.get("/explain/system-prompt")
async def get_explain_system_prompt(
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict[str, str]:
    # Endpoint is authenticated and workspace-scoped by token to avoid prompt leakage.
    _ = auth.workspace_id

    return {
        "systemPrompt": EXPLANATION_SYSTEM_PROMPT,
    }
