from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.assurance_states import ChainState
from app.core.audit import log_audit_event
from app.core.mode_guard import require_non_solo, require_plan
from app.core.security import (
    AuthContext,
    ensure_workspace_scope,
    get_current_auth_context,
)
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.services.integrity_service import compute_prompt_sha256, compute_record_hash
from app.services.record_lifecycle_service import (
    commitment_matches_record,
    get_latest_event,
    verify_event_signature,
)


router = APIRouter(prefix="/integrity", tags=["integrity"])
logger = logging.getLogger(__name__)


@router.get("/verify", dependencies=[Depends(require_non_solo), Depends(require_plan("plus"))])
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
            "chainState": ChainState.UNVERIFIABLE.value,
            "message": (
                "No hash-chained records found. Records written before the chain "
                "was enabled are skipped."
            ),
        }

    records_checked = 0
    prev_hash: str | None = None
    states = {"active": 0, "validly_redacted": 0, "validly_deleted": 0}

    for record in records:
        records_checked += 1
        lifecycle = record.lifecycle_state or "active"

        if lifecycle in ("redacted", "deleted"):
            # Content was scrubbed by a privacy operation. Trust requires a valid
            # signed lifecycle event whose committed digests still match the
            # record's stored commitments. Scrubbed content WITHOUT such an event
            # is the real tamper signal (PART 2 #10 / #11).
            event = await get_latest_event(session, workspace_id, str(record.uuid))
            expected_type = "deletion" if lifecycle == "deleted" else "redaction"
            valid = (
                event is not None
                and event.event_type == expected_type
                and verify_event_signature(event)
                and commitment_matches_record(event, record)
            )
            if not valid:
                await log_audit_event(
                    session,
                    workspace_id=workspace_id,
                    user_id=auth.subject,
                    action="integrity_verify_hash_break",
                    target_uuid=str(record.uuid),
                    details={"lifecycle_state": lifecycle, "reason": "missing_or_invalid_lifecycle_event"},
                )
                return {
                    "ok": False,
                    "records_checked": records_checked,
                    "first_break_uuid": str(record.uuid),
                    "states": states,
                    "chainState": ChainState.TAMPERED.value,
                    "message": (
                        f"Record {record.uuid} has scrubbed content but no valid signed "
                        f"{expected_type} event — possible tampering."
                    ),
                }
            states["validly_deleted" if lifecycle == "deleted" else "validly_redacted"] += 1
        else:
            # Active record: recompute the chain hash from its content. The
            # committed prompt digest is preferred over re-hashing prompt_messages
            # so the check is stable across schema-compatible storage changes.
            prompt_sha256 = record.prompt_sha256 or compute_prompt_sha256(record.prompt_messages)
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
            if expected_hash != record.record_hash:
                await log_audit_event(
                    session,
                    workspace_id=workspace_id,
                    user_id=auth.subject,
                    action="integrity_verify_hash_break",
                    target_uuid=str(record.uuid),
                    details={"expected_hash": expected_hash, "stored_hash": record.record_hash},
                )
                return {
                    "ok": False,
                    "records_checked": records_checked,
                    "first_break_uuid": str(record.uuid),
                    "states": states,
                    "chainState": ChainState.TAMPERED.value,
                    "message": (
                        f"Hash mismatch at record {record.uuid}: stored hash does not match "
                        "recomputed value — record may have been tampered with."
                    ),
                }
            states["active"] += 1

        if record.prev_hash != prev_hash:
            await log_audit_event(
                session,
                workspace_id=workspace_id,
                user_id=auth.subject,
                action="integrity_verify_chain_break",
                target_uuid=str(record.uuid),
                details={"expected_prev": prev_hash, "stored_prev": record.prev_hash},
            )
            return {
                "ok": False,
                "records_checked": records_checked,
                "first_break_uuid": str(record.uuid),
                "states": states,
                "chainState": ChainState.TAMPERED.value,
                "message": (
                    f"Chain break at record {record.uuid}: prev_hash does not link "
                    "to the preceding record."
                ),
            }

        prev_hash = record.record_hash

    # A chain that is entirely active records has no scrubbed content to
    # distinguish; a chain containing redacted/deleted records is intact but
    # only "tamper-evident" in the general sense described in the README —
    # never claim more than that (PART 1 #8 / PART 5 #58).
    chain_state = (
        ChainState.FULLY_AVAILABLE.value
        if states["validly_redacted"] == 0 and states["validly_deleted"] == 0
        else ChainState.LOCALLY_TAMPER_EVIDENT.value
    )

    return {
        "ok": True,
        "records_checked": records_checked,
        "first_break_uuid": None,
        "states": states,
        "chainState": chain_state,
        "message": (
            f"Chain verified across {records_checked} record(s) — no tampering detected "
            f"({states['active']} active, {states['validly_redacted']} validly redacted, "
            f"{states['validly_deleted']} validly deleted)."
        ),
    }


@router.post("/aibom", dependencies=[Depends(require_non_solo), Depends(require_plan("plus"))])
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
