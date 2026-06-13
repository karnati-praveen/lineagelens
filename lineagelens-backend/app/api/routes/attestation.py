from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import SignedAttestation, get_public_key_hex, verify_attestation
from app.db.models import Attestation
from app.db.session import get_db_session

router = APIRouter(prefix="/attestations", tags=["attestations"])
logger = logging.getLogger(__name__)


@router.get("/{attestation_id}/verify")
async def verify_attestation_endpoint(
    attestation_id: int,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """Public endpoint: cryptographically verify a stored attestation.

    No authentication required — intended for third-party auditors.
    Returns the public key alongside the validity result so callers can
    perform independent offline verification.
    """
    result = await session.execute(
        select(Attestation).where(Attestation.id == attestation_id)
    )
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attestation not found.")

    statement = json.loads(att.statement_json)
    signed = SignedAttestation(
        statement=statement,
        signature=att.signature,
        public_key_id=att.public_key_id,
    )
    valid = verify_attestation(signed)

    return {
        "id": att.id,
        "valid": valid,
        "public_key_id": att.public_key_id,
        "public_key_hex": get_public_key_hex(),
        "subject_type": att.subject_type,
        "subject_id": att.subject_id,
        "workspace_id": att.workspace_id,
        "prev_hash": att.prev_hash,
        "created_at": att.created_at.isoformat(),
    }
