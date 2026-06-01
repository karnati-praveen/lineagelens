from __future__ import annotations

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session

router = APIRouter(prefix="/api/v1/aibom", tags=["aibom"])


@router.get("/{record_id}/export", dependencies=[Depends(require_non_solo)])
async def export_aibom_for_record(
    record_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> Response:
    """Return the workspace AI-BOM as a downloadable JSON file.

    Looks up the provenance record by UUID, derives the workspace_id,
    generates the full signed AIBOM, and streams it back with a
    Content-Disposition: attachment header.

    Plus/Max only.
    """
    from app.services.aibom_service import generate_aibom

    result = await session.execute(
        select(ProvenanceRecord).where(ProvenanceRecord.uuid == record_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    ensure_workspace_scope(auth, record.workspace_id)

    aibom = await generate_aibom(session, record.workspace_id)

    await log_audit_event(
        session,
        workspace_id=record.workspace_id,
        user_id=auth.subject,
        action="aibom_export_record",
        details={"record_id": record_id, "total_records": aibom["summary"]["total_records"]},
    )

    content = json.dumps(aibom, indent=2, default=str)
    # Strip any characters that would break the quoted-string in Content-Disposition.
    ws_prefix = "".join(c for c in record.workspace_id[:8] if c not in ('"', "\\", "\r", "\n"))
    filename = f"aibom-{ws_prefix}-{date.today().isoformat()}.json"

    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
