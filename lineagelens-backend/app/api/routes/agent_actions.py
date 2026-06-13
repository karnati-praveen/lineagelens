"""Agent action ledger routes (F4).

POST /agent-actions  — proxy-ingested action batch (proxy static-token or JWT)
GET  /agent-actions  — workspace-scoped flight-recorder timeline
GET  /agent-actions/session/{session_key} — full session reconstruction
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context, get_ingest_auth_context
from app.db.session import get_db_session
from app.schemas.agent_actions import (
    AgentActionResponse,
    IngestAgentActionsPayload,
    IngestAgentActionsResponse,
    SessionReconstructionResponse,
)
from app.services.agent_action_service import get_session_reconstruction, list_actions, record_actions_batch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-actions", tags=["agent-actions"])

_MAX_LIST_LIMIT = 500


# ── POST /agent-actions ───────────────────────────────────────────────────────

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=IngestAgentActionsResponse,
)
async def ingest_agent_actions(
    payload: IngestAgentActionsPayload,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_ingest_auth_context)],
) -> IngestAgentActionsResponse:
    """Accept a batch of agent actions from the proxy (or directly from any authenticated client).

    Accepts both the proxy static-token auth path (same as /ingest) and a
    regular JWT so the endpoint is reachable from the dashboard or CLI too.
    Each action is hash-chained and checked for risky patterns before storage.
    """
    ensure_workspace_scope(auth, payload.workspaceId)

    if not payload.actions:
        return IngestAgentActionsResponse(recorded=0, skipped=0, workspaceId=payload.workspaceId)

    result = await record_actions_batch(
        session,
        workspace_id=payload.workspaceId,
        session_key=payload.sessionKey,
        prompt_context_id=payload.promptContextId,
        actions=payload.actions,
        actor_user_id=auth.subject if auth.token_type != "proxy" else None,
    )
    await session.commit()

    logger.info(
        "AGENT_ACTIONS_INGEST workspace=%s session=%s recorded=%d skipped=%d",
        payload.workspaceId,
        payload.sessionKey[:8],
        result.recorded,
        result.skipped,
    )
    return result


# ── GET /agent-actions ────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[AgentActionResponse],
    dependencies=[Depends(require_non_solo)],
)
async def get_agent_actions(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    sessionKey: str | None = Query(default=None, alias="sessionKey"),
    type: str | None = Query(default=None, description="Filter by action_type"),
    from_: str | None = Query(default=None, alias="from", description="ISO-8601 lower bound"),
    to: str | None = Query(default=None, description="ISO-8601 upper bound"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[AgentActionResponse]:
    """Return the flight-recorder action timeline for this workspace.

    Filter by session, action type, and/or time window.  Results are ordered
    by occurred_at ascending (chronological order within each session).
    """
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    try:
        if from_:
            from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00"))
        if to:
            to_dt = datetime.fromisoformat(to.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime filter: {exc}") from exc

    rows = await list_actions(
        session,
        workspace_id=auth.workspace_id,
        session_key=sessionKey,
        action_type=type,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
        offset=offset,
    )
    return [AgentActionResponse.model_validate(r) for r in rows]


# ── GET /agent-actions/session/{session_key} ──────────────────────────────────

@router.get(
    "/session/{session_key}",
    response_model=SessionReconstructionResponse,
    dependencies=[Depends(require_non_solo)],
)
async def get_session_actions(
    session_key: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> SessionReconstructionResponse:
    """Full session reconstruction: prompt → actions → resulting code.

    Returns all actions for the session plus the UUIDs of any provenance
    records (code ingest) that share the same session key.  Together these
    form the complete audit trail from the user's original prompt through
    every agent step to the code that landed in the repository.
    """
    actions, prov_uuids = await get_session_reconstruction(
        session,
        workspace_id=auth.workspace_id,
        session_key=session_key,
    )
    return SessionReconstructionResponse(
        sessionKey=session_key,
        workspaceId=auth.workspace_id,
        actionCount=len(actions),
        actions=[AgentActionResponse.model_validate(a) for a in actions],
        provenanceRecordUuids=prov_uuids,
    )
