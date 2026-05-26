from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import hash_password, create_access_token, create_refresh_token
from app.db.models import UserAccount, Workspace
from app.db.session import get_db_session

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "static")

router = APIRouter(tags=["setup"])

_DEFAULT_WORKSPACE_NAME = "My Workspace"


class SetupRequest(BaseModel):
    username: str
    password: str
    workspace_name: str = _DEFAULT_WORKSPACE_NAME


async def is_setup_complete(session: AsyncSession) -> bool:
    result = await session.execute(select(func.count()).select_from(UserAccount))
    return (result.scalar_one() or 0) > 0


@router.get("/setup", include_in_schema=False)
async def setup_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
):
    if await is_setup_complete(session):
        return RedirectResponse("/dashboard", status_code=302)
    return FileResponse(os.path.join(_STATIC_DIR, "setup.html"), media_type="text/html")


@router.post("/setup", include_in_schema=False)
async def run_setup(
    payload: SetupRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    if await is_setup_complete(session):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Setup already complete.")

    username = _normalize_username(payload.username)
    _validate_password(payload.password, settings)

    workspace_name = (payload.workspace_name or _DEFAULT_WORKSPACE_NAME).strip()[:128] or _DEFAULT_WORKSPACE_NAME

    # Derive the workspace_id from the workspace NAME the user just typed,
    # not from a random hex suffix. This way "testing" stays "testing" and
    # the user can type the same thing they remember when logging in later.
    # Fall back to the username slug if the name slug is empty (e.g. all
    # punctuation), and to a random short id only as last resort.
    workspace_id = _slugify_workspace_id(workspace_name) \
        or _slugify_workspace_id(username) \
        or f"ws-{uuid.uuid4().hex[:12]}"

    # First-time setup only: there should be no existing workspace, but
    # guard against a collision if somehow one exists (e.g. partial prior
    # setup that committed the workspace but not the user).
    existing = await session.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )
    if existing.scalar_one_or_none() is not None:
        workspace_id = f"{workspace_id}-{uuid.uuid4().hex[:6]}"

    refresh_jti = uuid.uuid4().hex
    user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        workspace_id=workspace_id,
        role="admin",
        is_active=True,
        refresh_token_jti=refresh_jti,
    )
    session.add(user)

    try:
        # Flush to populate user.id so we can wire it to the workspace as owner
        # in a SINGLE atomic commit below. The previous two-commit pattern could
        # leave the workspace with owner_id=NULL if the second commit failed.
        await session.flush()
        await session.refresh(user)

        ws = Workspace(id=workspace_id, name=workspace_name, owner_id=str(user.id))
        session.add(ws)

        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or workspace already exists.",
        )
    except Exception as exc:
        await session.rollback()
        logger.exception("Setup failed during database commit: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Setup failed: {type(exc).__name__}. Check the server logs for details.",
        ) from exc

    # Mark setup complete in app state so the guard skips DB checks
    request.app.state.setup_complete = True

    scopes = sorted(settings.required_scopes_set)
    token_version = user.token_version or 0

    access_token, access_expires_at = create_access_token(
        subject=str(user.id),
        workspace_id=workspace_id,
        scopes=scopes,
        settings=settings,
        extra_claims={"username": username, "role": "admin", "token_version": token_version},
    )
    refresh_token, _ = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace_id,
        settings=settings,
        extra_claims={"username": username, "role": "admin", "token_version": token_version, "jti": refresh_jti},
    )

    return JSONResponse(
        status_code=201,
        content={
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "workspaceId": workspace_id,
            "username": username,
        },
    )


def _normalize_username(raw: str) -> str:
    username = (raw or "").strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")
    if len(username) > 128:
        raise HTTPException(status_code=400, detail="Username must be at most 128 characters.")
    if not re.fullmatch(r"[a-z0-9._@-]+", username):
        raise HTTPException(status_code=400, detail="Username may only contain letters, numbers, dots, hyphens, underscores, and @.")
    return username


def _validate_password(password: str, settings: Settings) -> None:
    minimum = max(8, settings.auth_password_min_length)
    if len(password) < minimum:
        raise HTTPException(status_code=400, detail=f"Password must be at least {minimum} characters.")


def _slugify_workspace_id(raw: str) -> str:
    """Turn a user-provided string into a stable, memorable workspace_id.

    'My Workspace' -> 'my-workspace'
    'testing!!!'   -> 'testing'
    'admin'        -> 'admin'
    ''             -> ''
    """
    if not raw:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:120]
