from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.config import Settings, get_settings
from app.core.mode_guard import require_non_solo
from app.core.security import (
    AuthContext,
    ensure_workspace_scope,
    get_current_auth_context,
)
from app.db.models import ProvenanceRecord
from app.db.models import WitnessReceipt as WitnessReceiptRow
from app.db.session import get_db_session
from app.services.witness_service import compute_periodic_root, witness_root

router = APIRouter(
    prefix="/witness",
    tags=["witness"],
    dependencies=[Depends(require_non_solo)],
)
logger = logging.getLogger(__name__)


def _serialize(row: WitnessReceiptRow) -> dict:
    return {
        "backend": row.backend,
        "status": row.status,
        "externalRef": row.external_ref,
        "details": row.details,
        "rootHash": row.root_hash,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


async def _current_root(session: AsyncSession, workspace_id: str) -> str:
    result = await session.execute(
        select(ProvenanceRecord.record_hash).where(
            and_(
                ProvenanceRecord.workspace_id == workspace_id,
                ProvenanceRecord.record_hash.is_not(None),
            )
        )
    )
    hashes = sorted(h for (h,) in result.all() if h)
    return compute_periodic_root(hashes)


@router.post("/publish")
async def publish_witness_round(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    settings: Annotated[Settings, Depends(get_settings)],
    workspace_id: str = Query(...),
) -> dict:
    """Publish the workspace's current chain root to every configured witness
    backend (PART 5 #53). Every backend's receipt is returned and persisted,
    including `not_configured` ones — nothing is hidden."""
    ensure_workspace_scope(auth, workspace_id)

    root_hash = await _current_root(session, workspace_id)
    receipts = await witness_root(root_hash, settings=settings)

    rows = []
    for receipt in receipts:
        row = WitnessReceiptRow(
            workspace_id=workspace_id,
            root_hash=root_hash,
            backend=receipt.backend,
            status=receipt.status,
            external_ref=receipt.external_ref,
            details=receipt.details,
        )
        session.add(row)
        rows.append(row)

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=auth.subject,
        action="witness.publish",
        details={"rootHash": root_hash, "statuses": {r.backend: r.status for r in receipts}},
    )
    await session.commit()

    return {
        "rootHash": root_hash,
        "receipts": [
            {
                "backend": r.backend,
                "status": r.status,
                "externalRef": r.external_ref,
                "details": r.details,
            }
            for r in receipts
        ],
    }


@router.get("/receipts")
async def list_witness_receipts(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    ensure_workspace_scope(auth, workspace_id)
    result = await session.execute(
        select(WitnessReceiptRow)
        .where(WitnessReceiptRow.workspace_id == workspace_id)
        .order_by(desc(WitnessReceiptRow.created_at))
        .limit(limit)
    )
    return {"items": [_serialize(r) for r in result.scalars().all()]}
