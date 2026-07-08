from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import (
    KeyRecord,
    get_current_public_key_id,
    key_status_at,
    register_key,
    revoke_key,
)
from app.core.audit import log_audit_event
from app.core.mode_guard import require_non_solo
from app.core.security import AuthContext, require_admin
from app.db.models import AttestationKey
from app.db.session import get_db_session

router = APIRouter(
    prefix="/admin/keys",
    tags=["key-registry"],
    dependencies=[Depends(require_non_solo)],
)
logger = logging.getLogger(__name__)


class KeyRegisterRequest(BaseModel):
    public_key_id: str = Field(..., alias="publicKeyId", min_length=1, max_length=64)
    public_key_hex: str = Field(..., alias="publicKeyHex", min_length=1, max_length=64)
    valid_from: datetime | None = Field(default=None, alias="validFrom")
    label: str | None = Field(default=None, max_length=128)

    model_config = ConfigDict(populate_by_name=True)


class KeyRevokeRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=512)


def _serialize_key(row: AttestationKey) -> dict:
    record = KeyRecord(
        public_key_id=row.public_key_id,
        public_key_hex=row.public_key_hex,
        valid_from=row.valid_from.isoformat() if row.valid_from else None,
        valid_until=row.valid_until.isoformat() if row.valid_until else None,
        compromised_at=row.compromised_at.isoformat() if row.compromised_at else None,
        status=row.status,
    )
    return {
        "id": row.id,
        "publicKeyId": row.public_key_id,
        "publicKeyHex": row.public_key_hex,
        "validFrom": row.valid_from.isoformat() if row.valid_from else None,
        "validUntil": row.valid_until.isoformat() if row.valid_until else None,
        "compromisedAt": row.compromised_at.isoformat() if row.compromised_at else None,
        "status": row.status,
        "label": row.label,
        "revokedBy": row.revoked_by,
        "revocationReason": row.revocation_reason,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "currentTrustStatus": key_status_at(record, None),
    }


@router.get("")
async def list_keys(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """List every DB-registered attestation key (active, retired, compromised).

    The currently-active signing key (derived from ATTESTATION_SIGNING_KEY /
    JWT_SECRET_KEY, PART 3 #19) is surfaced separately since it may predate
    this registry existing.
    """
    result = await session.execute(select(AttestationKey).order_by(AttestationKey.id))
    rows = result.scalars().all()
    return {
        "keys": [_serialize_key(r) for r in rows],
        "currentActivePublicKeyId": get_current_public_key_id(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_key(
    payload: KeyRegisterRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Register a new signing key ahead of a rotation."""
    existing = await session.execute(
        select(AttestationKey).where(AttestationKey.public_key_id == payload.public_key_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A key with this publicKeyId already exists.")

    row = await register_key(
        session,
        public_key_id=payload.public_key_id,
        public_key_hex=payload.public_key_hex,
        valid_from=payload.valid_from,
        label=payload.label,
    )
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="key_registry.register",
        target_uuid=payload.public_key_id,
        details={"label": payload.label},
    )
    await session.commit()
    await session.refresh(row)
    return _serialize_key(row)


@router.get("/{public_key_id}")
async def get_key(
    public_key_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    result = await session.execute(
        select(AttestationKey).where(AttestationKey.public_key_id == public_key_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown publicKeyId.")
    return _serialize_key(row)


@router.post("/{public_key_id}/revoke")
async def revoke_key_route(
    public_key_id: str,
    payload: KeyRevokeRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Revoke a key at runtime — no redeploy required (PART 5 #57).

    Any signature made after this call will be rejected by
    verify_attestation_detailed(...) even though the cryptographic math still
    checks out.
    """
    try:
        row = await revoke_key(
            session,
            public_key_id,
            reason=payload.reason,
            revoked_by=auth.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="key_registry.revoke",
        target_uuid=public_key_id,
        details={"reason": payload.reason},
    )
    await session.commit()
    await session.refresh(row)
    return _serialize_key(row)
