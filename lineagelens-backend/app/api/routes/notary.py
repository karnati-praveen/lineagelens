from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context, ensure_workspace_scope
from app.db.models import ProvenanceRecord, EpochNotarization
from app.db.session import get_db_session
from app.services.tsa_service import compute_merkle_root, request_rfc3161_timestamp

router = APIRouter(prefix="/integrity", tags=["integrity", "notary"])
logger = logging.getLogger(__name__)

@router.post("/notarize")
async def notarize_epoch(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
) -> dict:
    """
    Compute a Merkle root of all record hashes in the workspace and obtain an RFC 3161 Timestamp.
    This creates an EpochNotarization receipt proving the records existed at the time of notarization.
    """
    ensure_workspace_scope(auth, workspace_id)

    # 1. Fetch all record hashes for the workspace
    stmt = (
        select(ProvenanceRecord.record_hash, ProvenanceRecord.uuid)
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
            ProvenanceRecord.record_hash.is_not(None)
        )
        .order_by(ProvenanceRecord.id.asc())
    )
    result = await session.execute(stmt)
    records = result.all()

    if not records:
        return {"ok": False, "message": "No chained records found to notarize."}

    hashes = [r.record_hash for r in records if r.record_hash]
    if not hashes:
        return {"ok": False, "message": "No valid record hashes found to notarize."}

    # 2. Compute the Merkle Root
    merkle_root = compute_merkle_root(hashes)
    if not merkle_root:
        raise HTTPException(status_code=500, detail="Failed to compute Merkle Root.")

    # 3. Check if this root is already notarized
    existing_stmt = select(EpochNotarization).where(
        EpochNotarization.workspace_id == workspace_id,
        EpochNotarization.merkle_root == merkle_root
    )
    existing_result = await session.execute(existing_stmt)
    if existing_result.scalar_one_or_none() is not None:
        return {"ok": True, "message": "Epoch already notarized.", "merkle_root": merkle_root}

    # 4. Request RFC 3161 Timestamp
    try:
        tsa_token = await request_rfc3161_timestamp(merkle_root)
    except Exception as e:
        logger.error(f"TSA Notarization failed: {e}")
        raise HTTPException(status_code=502, detail="Time Stamping Authority request failed.")

    # 5. Save the EpochNotarization Receipt (real TSA token only — never mock).
    epoch_record = EpochNotarization(
        workspace_id=workspace_id,
        merkle_root=merkle_root,
        start_record_uuid=str(records[0].uuid),
        end_record_uuid=str(records[-1].uuid),
        tsa_token=tsa_token,
        tsa_provider="rfc3161-tsa"
    )
    session.add(epoch_record)
    await session.commit()

    return {
        "ok": True,
        "message": "Epoch notarized successfully.",
        "merkle_root": merkle_root,
        "records_included": len(hashes)
    }
