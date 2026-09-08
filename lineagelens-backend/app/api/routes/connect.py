"""Easy-connect endpoints: pairing-code management and redemption.

Admins create codes (from the /connect dashboard page or via API); any client
redeems a code for a scoped API key. See app/services/pairing_service.py for
the security model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import AuthContext, get_client_ip, get_current_auth_context, require_admin
from app.db.models import PairingCode, Workspace
from app.db.session import get_db_session
from app.services.pairing_service import (
    ALLOWED_CLIENTS,
    MAX_TTL_MINUTES,
    MAX_USES,
    PairingError,
    _as_utc,
    create_pairing_code,
    redeem_pairing_code,
    revoke_pairing_code,
)

router = APIRouter(tags=["connect"])


def _ser(p: PairingCode) -> dict:
    now = datetime.now(UTC)
    expires_at = _as_utc(p.expires_at)
    return {
        "id": str(p.id),
        "client": p.client,
        "label": p.label,
        "scopes": p.scopes,
        "maxUses": p.max_uses,
        "useCount": p.use_count,
        "attempts": p.attempts,
        "expiresAt": p.expires_at.isoformat(),
        "revokedAt": p.revoked_at.isoformat() if p.revoked_at else None,
        "createdAt": p.created_at.isoformat(),
        "lastRedeemedLabel": p.last_redeemed_label,
        "status": (
            "revoked"
            if p.revoked_at is not None
            else "expired"
            if expires_at <= now
            else "exhausted"
            if p.use_count >= p.max_uses
            else "active"
        ),
    }


class ConnectCodeCreate(BaseModel):
    client: str = Field(..., description=f"One of: {sorted(ALLOWED_CLIENTS)}")
    label: str | None = Field(default=None, max_length=256)
    ttlMinutes: int = Field(default=10, ge=1, le=MAX_TTL_MINUTES)
    maxUses: int = Field(default=1, ge=1, le=MAX_USES)

    model_config = ConfigDict(populate_by_name=True)


class ConnectCodeRedeem(BaseModel):
    code: str = Field(..., min_length=4, max_length=64)
    client: str = Field(..., description=f"One of: {sorted(ALLOWED_CLIENTS)}")
    label: str | None = Field(default=None, max_length=256)

    model_config = ConfigDict(populate_by_name=True)


@router.post("/connect/codes", status_code=status.HTTP_201_CREATED)
async def create_connect_code(
    payload: ConnectCodeCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Mint a pairing code for the caller's workspace. The plaintext code is shown once."""
    try:
        pairing, code = await create_pairing_code(
            session,
            workspace_id=auth.workspace_id,
            created_by=auth.subject,
            client=payload.client,
            label=payload.label,
            ttl_minutes=payload.ttlMinutes,
            max_uses=payload.maxUses,
        )
    except PairingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await log_client_action(
        session, auth=auth, request=request, settings=settings, action="pairing.code_created_http"
    )
    response = _ser(pairing)
    response["code"] = code  # plaintext, only on creation
    return response


@router.get("/connect/codes")
async def list_connect_codes(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    result = await session.execute(
        select(PairingCode)
        .where(PairingCode.workspace_id == auth.workspace_id)
        .order_by(PairingCode.created_at.desc())
        .limit(50)
    )
    codes = list(result.scalars().all())
    return {"results": [_ser(p) for p in codes], "count": len(codes)}


@router.delete("/connect/codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def revoke_connect_code(
    code_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> None:
    try:
        await revoke_pairing_code(
            session, pairing_id=code_id, workspace_id=auth.workspace_id, revoked_by=auth.subject
        )
    except PairingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/connect/redeem")
async def redeem_connect_code(
    payload: ConnectCodeRedeem,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """Exchange a pairing code for a scoped API key. Unauthenticated but strictly rate-limited."""
    try:
        ak, pairing, full_key = await redeem_pairing_code(
            session, raw_code=payload.code, client=payload.client, label=payload.label
        )
    except PairingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    ws_name = None
    ws = await session.get(Workspace, pairing.workspace_id)
    if ws is not None:
        ws_name = ws.name

    return {
        "apiKey": full_key,
        "keyId": str(ak.id),
        "keyPrefix": ak.key_prefix,
        "scopes": ak.scopes,
        "workspaceId": pairing.workspace_id,
        "workspaceName": ws_name,
        "serverTier": None,  # filled from /health by clients; kept for symmetry
    }


async def log_client_action(
    session: AsyncSession,
    *,
    auth: AuthContext,
    request: Request,
    settings: Settings,
    action: str,
) -> None:
    """Keep the HTTP-layer audit entry (IP attribution) out of the service layer."""
    from app.core.audit import log_audit_event

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action=action,
        ip_address=get_client_ip(request, settings),
    )
