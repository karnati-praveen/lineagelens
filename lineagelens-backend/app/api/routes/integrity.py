from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.mode_guard import require_non_solo
from app.core.security import (
    AuthContext,
    ensure_workspace_scope,
    get_current_auth_context,
)
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.services.integrity_service import compute_prompt_sha256, compute_record_hash


router = APIRouter(prefix="/integrity", tags=["integrity"])
logger = logging.getLogger(__name__)


@router.get("/verify", dependencies=[Depends(require_non_solo)])
async def verify_chain(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
) -> dict:
    """Walk the hash chain for a workspace and report any tampered records.

    Only records that have a record_hash are verified — records written before
    the chain was enabled (Lite upgrades, pre-migration rows) are silently skipped.

    Plus/Max only.
    """
    ensure_workspace_scope(auth, workspace_id)

    stmt = (
        select(ProvenanceRecord)
        .where(
            and_(
                ProvenanceRecord.workspace_id == workspace_id,
                ProvenanceRecord.record_hash.is_not(None),
            )
        )
        .order_by(asc(ProvenanceRecord.id))
    )
    result = await session.execute(stmt)
    records = result.scalars().all()

    if not records:
        return {
            "ok": True,
            "records_checked": 0,
            "first_break_uuid": None,
            "message": (
                "No hash-chained records found. Records written before the chain "
                "was enabled are skipped."
            ),
        }

    records_checked = 0
    prev_hash: str | None = None

    for record in records:
        prompt_sha256 = compute_prompt_sha256(record.prompt_messages)
        expected_hash = compute_record_hash(
            record_uuid=str(record.uuid),
            workspace_id=record.workspace_id,
            file_path=record.file_path,
            inserted_code=record.inserted_code,
            model_name=record.model_name,
            prompt_sha256=prompt_sha256,
            timestamp_iso=record.timestamp_iso.isoformat(),
            prev_hash=prev_hash,
        )
        records_checked += 1

        if expected_hash != record.record_hash:
            await log_audit_event(
                session,
                workspace_id=workspace_id,
                user_id=auth.subject,
                action="integrity_verify_hash_break",
                target_uuid=str(record.uuid),
                details={
                    "expected_hash": expected_hash,
                    "stored_hash": record.record_hash,
                },
            )
            return {
                "ok": False,
                "records_checked": records_checked,
                "first_break_uuid": str(record.uuid),
                "message": (
                    f"Hash mismatch at record {record.uuid}: stored hash does not match "
                    "recomputed value — record may have been tampered with."
                ),
            }

        if record.prev_hash != prev_hash:
            await log_audit_event(
                session,
                workspace_id=workspace_id,
                user_id=auth.subject,
                action="integrity_verify_chain_break",
                target_uuid=str(record.uuid),
                details={
                    "expected_prev": prev_hash,
                    "stored_prev": record.prev_hash,
                },
            )
            return {
                "ok": False,
                "records_checked": records_checked,
                "first_break_uuid": str(record.uuid),
                "message": (
                    f"Chain break at record {record.uuid}: prev_hash does not link "
                    "to the preceding record."
                ),
            }

        prev_hash = record.record_hash

    return {
        "ok": True,
        "records_checked": records_checked,
        "first_break_uuid": None,
        "message": f"Chain verified across {records_checked} record(s) — no tampering detected.",
    }


@router.post("/aibom", dependencies=[Depends(require_non_solo)])
async def export_aibom(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    """Generate and return a signed AI Bill of Materials for the workspace.

    The response includes a summary (% AI-authored, per-model breakdown,
    disclosure coverage, chain verified) and a per-record entry list signed
    with HMAC-SHA256 so recipients can verify the document was not modified
    after generation.

    Plus/Max only.
    """
    ensure_workspace_scope(auth, workspace_id)

    from app.services.aibom_service import generate_aibom

    aibom = await generate_aibom(
        session=session,
        workspace_id=workspace_id,
        date_from=date_from,
        date_to=date_to,
    )

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=auth.subject,
        action="aibom_export",
        details={
            "total_records": aibom["summary"]["total_records"],
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        },
    )

    return aibom
