from __future__ import annotations

import logging
import uuid as uuid_pkg
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.mode_guard import require_non_solo, require_plan
from app.core.security import (
    AuthContext,
    ensure_workspace_scope,
    get_current_auth_context,
)
from app.db.models import ContinuityDrill
from app.db.session import get_db_session
from app.services.continuity_drill_service import run_continuity_drill

router = APIRouter(
    prefix="/continuity-drills",
    tags=["continuity-drills"],
    dependencies=[Depends(require_non_solo), Depends(require_plan("plus"))],
)
logger = logging.getLogger(__name__)


def _serialize(row: ContinuityDrill) -> dict:
    return {
        "publicRef": str(row.public_ref),
        "workspaceId": row.workspace_id,
        "overallStatus": row.overall_status,
        "steps": row.steps_json,
        "signature": row.signature,
        "publicKeyId": row.public_key_id,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("")
async def trigger_drill(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
) -> dict:
    """Run every continuity-drill step and persist a signed result (PART 5 #55).

    Never reports a bare "all good" — the response's overallStatus is
    green/amber/red, and each step is passed/failed/skipped_not_configured.
    """
    ensure_workspace_scope(auth, workspace_id)

    neo4j_service = getattr(request.app.state, "neo4j_service", None)
    result = await run_continuity_drill(session, workspace_id, neo4j_service=neo4j_service)

    row = ContinuityDrill(
        workspace_id=workspace_id,
        overall_status=result.overall_status,
        steps_json=[s.to_dict() for s in result.steps],
        signature=result.signature,
        public_key_id=result.public_key_id,
        created_by=auth.subject,
    )
    session.add(row)
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=auth.subject,
        action="continuity_drill.run",
        details={"overallStatus": result.overall_status},
    )
    await session.commit()
    await session.refresh(row)

    return _serialize(row)


@router.get("/{public_ref}")
async def get_drill(
    public_ref: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    try:
        parsed_ref = uuid_pkg.UUID(public_ref)
    except ValueError:
        raise HTTPException(status_code=404, detail="Drill result not found.")

    result = await session.execute(
        select(ContinuityDrill).where(ContinuityDrill.public_ref == parsed_ref)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Drill result not found.")

    ensure_workspace_scope(auth, row.workspace_id)
    return _serialize(row)


@router.get("")
async def list_drills(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    ensure_workspace_scope(auth, workspace_id)
    result = await session.execute(
        select(ContinuityDrill)
        .where(ContinuityDrill.workspace_id == workspace_id)
        .order_by(desc(ContinuityDrill.created_at))
        .limit(limit)
    )
    return {"items": [_serialize(r) for r in result.scalars().all()]}
