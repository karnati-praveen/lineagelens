"""Lead capture: unauthenticated POST /leads for VS Code extension email sign-up.

Rate-limited (reuses require_auth_rate_limit — same per-IP cap as auth endpoints).
Upserts on email so re-submitting the same address is idempotent.
Email is treated as PII: never logged beyond a debug source tag.
"""
from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import get_client_ip, require_auth_rate_limit
from app.db.models import Lead
from app.db.session import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])

_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{1,63}$")
_MAX_EMAIL = 254
_MAX_SOURCE = 64
_MAX_VERSION = 32


class LeadRequest(BaseModel):
    email: str
    source: str = "vscode-extension"
    extension_version: str | None = None


def _normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > _MAX_EMAIL:
        raise HTTPException(status_code=400, detail="Email address too long.")
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    return email


@router.post("/leads", status_code=200, dependencies=[Depends(require_auth_rate_limit)])
async def capture_lead(
    payload: LeadRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    """Idempotent upsert of a lead email.  Re-submitting the same email updates
    source/updated_at and returns the same success response — no error, no duplicate row."""
    email = _normalize_email(payload.email)
    source = (payload.source or "unknown").strip()[:_MAX_SOURCE]
    version = ((payload.extension_version or "").strip()[:_MAX_VERSION]) or None

    settings = get_settings()
    ip: str | None = None
    if settings.app_env.strip().lower() != "test":
        ip = get_client_ip(request, settings)

    existing = await session.scalar(select(Lead).where(Lead.email == email))
    if existing is not None:
        existing.source = source
        if version:
            existing.extension_version = version
    else:
        session.add(Lead(email=email, source=source, extension_version=version, ip=ip))

    await session.commit()
    logger.debug("Lead captured from source=%s", source)
    return {"saved": True}


@router.delete("/leads", status_code=200, dependencies=[Depends(require_auth_rate_limit)])
async def remove_lead(
    email: Annotated[str, Query(description="Email address to remove")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    """Opt-out: remove the lead by email query param.  Returns success even if email is not found."""
    email = _normalize_email(email)
    existing = await session.scalar(select(Lead).where(Lead.email == email))
    if existing is not None:
        await session.delete(existing)
        await session.commit()
    return {"removed": True}
