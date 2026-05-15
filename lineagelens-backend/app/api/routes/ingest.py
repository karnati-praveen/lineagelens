from __future__ import annotations

import logging
import uuid as uuid_stdlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.schemas.provenance import IngestResponse
from app.services.ingest_normalizer import extract_workspace_id, normalize_ingest_payload
from app.services.provenance_service import find_existing_ingest_record, ingest_provenance_event


router = APIRouter(tags=["ingest"])
logger = logging.getLogger(__name__)


@router.post("/ingest")
async def ingest_provenance(
    payload: dict[str, Any],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> IngestResponse:
    requested_workspace = extract_workspace_id(payload)
    ensure_workspace_scope(auth, requested_workspace)

    # Fast-path: if the client sent X-Idempotency-Key, check for an existing record
    # before normalizing — avoids CPU work on every retry.
    idempotency_key = (request.headers.get("x-idempotency-key") or "").strip()
    if idempotency_key:
        try:
            idempotency_uuid = uuid_stdlib.UUID(idempotency_key)
        except (ValueError, AttributeError):
            idempotency_uuid = None
        if idempotency_uuid is not None:
            existing = await find_existing_ingest_record(
                session=session,
                workspace_id=auth.workspace_id,
                record_uuid=idempotency_uuid,
                request_uuid=None,
            )
            if existing is not None:
                return IngestResponse(
                    uuid=str(existing.uuid),
                    workspaceId=existing.workspace_id,
                    lineageNodeId=existing.lineage_node_id,
                    stored=False,
                    warnings=["Duplicate request; existing record returned."],
                )

    try:
        normalized_payload = normalize_ingest_payload(payload, workspace_id=auth.workspace_id)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    neo4j_service = request.app.state.neo4j_service
    settings = request.app.state.settings

    try:
        outcome = await ingest_provenance_event(
            session=session,
            payload=normalized_payload,
            auth=auth,
            settings=settings,
            neo4j_service=neo4j_service,
            app_state=request.app.state,
        )
    except Exception:
        logger.exception(
            "Failed to ingest provenance payload: workspace=%s request_uuid=%s file=%s",
            auth.workspace_id,
            normalized_payload.request_uuid,
            normalized_payload.file_path,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest provenance payload.",
        )

    return IngestResponse(
        uuid=str(outcome.record.uuid),
        workspaceId=outcome.record.workspace_id,
        lineageNodeId=outcome.record.lineage_node_id,
        stored=outcome.stored,
        warnings=outcome.warnings,
    )
