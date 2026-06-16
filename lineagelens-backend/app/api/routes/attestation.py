from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import SignedAttestation, get_public_key_hex, verify_attestation
from app.core.security import AuthContext, get_current_auth_context
from app.db.models import Attestation
from app.db.session import get_db_session

router = APIRouter(prefix="/attestations", tags=["attestations"])
logger = logging.getLogger(__name__)


def _parse_public_ref(raw: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attestation not found.")


@router.get("/{public_ref}/verify")
async def verify_attestation_endpoint(
    public_ref: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """Public endpoint: cryptographically verify a stored attestation.

    No authentication required — intended for third-party auditors.
    Returns only the minimum fields needed for independent verification:
    id (the non-enumerable public_ref UUID), validity flag, and the public key.
    Workspace, subject, and hash-chain fields are intentionally omitted to
    prevent cross-tenant metadata enumeration.
    """
    parsed = _parse_public_ref(public_ref)
    result = await session.execute(
        select(Attestation).where(Attestation.public_ref == parsed)
    )
    att = result.scalar_one_or_none()
    if att is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attestation not found.")

    signed = SignedAttestation(
        statement=json.loads(att.statement_json),
        signature=att.signature,
        public_key_id=att.public_key_id,
    )
    valid = verify_attestation(signed)

    # Safe fields only — workspace_id, subject_id, prev_hash, subject_type,
    # created_at are deliberately excluded from the unauthenticated response.
    return {
        "id": str(att.public_ref),
        "valid": valid,
        "public_key_id": att.public_key_id,
        "public_key_hex": get_public_key_hex(),
    }


@router.get("/{public_ref}")
async def get_attestation(
    public_ref: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Authenticated: return full attestation detail. Workspace-scoped — 404s for
    attestations that belong to a different workspace so the existence of
    cross-tenant attestations is not revealed.
    """
    parsed = _parse_public_ref(public_ref)
    result = await session.execute(
        select(Attestation).where(Attestation.public_ref == parsed)
    )
    att = result.scalar_one_or_none()
    if att is None or att.workspace_id != auth.workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attestation not found.")

    signed = SignedAttestation(
        statement=json.loads(att.statement_json),
        signature=att.signature,
        public_key_id=att.public_key_id,
    )
    valid = verify_attestation(signed)

    return {
        "id": str(att.public_ref),
        "valid": valid,
        "public_key_id": att.public_key_id,
        "public_key_hex": get_public_key_hex(),
        "subject_type": att.subject_type,
        "subject_id": att.subject_id,
        "workspace_id": att.workspace_id,
        "prev_hash": att.prev_hash,
        "created_at": att.created_at.isoformat(),
    }
