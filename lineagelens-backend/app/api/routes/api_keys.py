from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context
from app.db.models import ApiKey
from app.db.session import get_db_session

router = APIRouter(tags=["api-keys"])
logger = logging.getLogger(__name__)

KEY_PREFIX = "llk_"  # lineagelens key prefix


def _generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, sha256_hash)."""
    raw = secrets.token_urlsafe(32)
    full_key = KEY_PREFIX + raw
    prefix = full_key[:12]
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, key_hash


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_days: int | None = Field(default=None, alias="expiresDays", ge=1, le=3650)

    model_config = ConfigDict(populate_by_name=True)


def _ser(k: ApiKey, full_key: str | None = None) -> dict:
    d = {
        "id": str(k.id),
        "name": k.name,
        "keyPrefix": k.key_prefix,
        "scopes": k.scopes,
        "isActive": k.is_active,
        "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
        "expiresAt": k.expires_at.isoformat() if k.expires_at else None,
        "createdAt": k.created_at.isoformat() if k.created_at else None,
    }
    if full_key:
        d["key"] = full_key  # only shown on creation
    return d


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Create a new API key for the authenticated user. The full key is shown ONCE."""
    full_key, prefix, key_hash = _generate_api_key()

    expires_at = None
    if payload.expires_days:
        from datetime import timedelta

        expires_at = datetime.now(UTC) + timedelta(days=payload.expires_days)

    ak = ApiKey(
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        name=payload.name,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=payload.scopes,
        expires_at=expires_at,
        is_active=True,
    )
    session.add(ak)
    await session.commit()
    await session.refresh(ak)
    return _ser(ak, full_key=full_key)


@router.get("/api-keys")
async def list_api_keys(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    result = await session.execute(
        select(ApiKey)
        .where(
            ApiKey.workspace_id == auth.workspace_id,
            ApiKey.user_id == auth.subject,
        )
        .order_by(ApiKey.created_at.desc())
    )
    keys = list(result.scalars().all())
    return {"results": [_ser(k) for k in keys], "count": len(keys)}


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> None:
    import uuid as uuid_pkg

    try:
        kid = uuid_pkg.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key ID.")

    result = await session.execute(
        select(ApiKey).where(
            ApiKey.id == kid,
            ApiKey.workspace_id == auth.workspace_id,
            ApiKey.user_id == auth.subject,
        )
    )
    ak = result.scalar_one_or_none()
    if ak is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    ak.is_active = False
    await session.commit()
