from __future__ import annotations

import logging
import secrets
import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.config import Settings, get_settings
from app.core.security import (
    AuthContext,
    create_access_token,
    create_refresh_token,
    get_current_auth_context,
    hash_password,
    require_admin,
)
from app.db.models import OidcProvider, UserAccount
from app.db.session import get_db_session
from app.services.oidc_service import (
    build_auth_url,
    consume_state_async,
    exchange_code,
    fetch_discovery_doc,
    fetch_userinfo,
    store_state_async,
)

router = APIRouter(prefix="/auth/sso", tags=["sso"])
logger = logging.getLogger(__name__)

_PROVIDER_NOT_FOUND = "Provider not found."


class OidcProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    issuer: str = Field(..., min_length=1, max_length=512)
    client_id: str = Field(..., min_length=1, max_length=256, alias="clientId")
    client_secret: str = Field(..., min_length=1, max_length=512, alias="clientSecret")
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    default_role: str = Field(default="member", alias="defaultRole")
    enabled: bool = True

    model_config = ConfigDict(populate_by_name=True)


def _ser(p: OidcProvider) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "issuer": p.issuer,
        "clientId": p.client_id,
        "scopes": p.scopes,
        "defaultRole": p.default_role,
        "enabled": p.enabled,
        "createdAt": p.created_at.isoformat(),
    }


@router.post("/providers", status_code=status.HTTP_201_CREATED)
async def create_oidc_provider(
    payload: OidcProviderCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Configure an OIDC/OAuth2 SSO provider for this workspace (admin only)."""
    try:
        await fetch_discovery_doc(payload.issuer)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not fetch OIDC discovery document from '{payload.issuer}': {exc}",
        )

    provider = OidcProvider(
        workspace_id=auth.workspace_id,
        name=payload.name,
        issuer=payload.issuer,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        scopes=payload.scopes,
        default_role=payload.default_role,
        enabled=payload.enabled,
        created_by=auth.subject,
    )
    session.add(provider)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="sso.provider.create",
        details={"name": payload.name, "issuer": payload.issuer},
    )
    await session.commit()
    await session.refresh(provider)
    return _ser(provider)


@router.get("/providers")
async def list_oidc_providers(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    result = await session.execute(
        select(OidcProvider).where(OidcProvider.workspace_id == auth.workspace_id)
    )
    providers = list(result.scalars().all())
    return {"results": [_ser(p) for p in providers], "count": len(providers)}


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_oidc_provider(
    provider_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> None:
    try:
        parsed_id = uuid_pkg.UUID(provider_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)

    result = await session.execute(
        select(OidcProvider).where(
            OidcProvider.id == parsed_id,
            OidcProvider.workspace_id == auth.workspace_id,
        )
    )
    p = result.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="sso.provider.delete",
        details={"name": p.name},
    )
    await session.delete(p)
    await session.commit()


@router.get("/login/{provider_id}")
async def sso_login(
    provider_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RedirectResponse:
    """Initiate OIDC login. Redirects the browser to the configured IdP."""
    try:
        parsed_id = uuid_pkg.UUID(provider_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=_PROVIDER_NOT_FOUND)

    result = await session.execute(
        select(OidcProvider).where(
            OidcProvider.id == parsed_id,
            OidcProvider.enabled.is_(True),
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found or disabled.")

    try:
        discovery_doc = await fetch_discovery_doc(provider.issuer)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OIDC discovery failed: {exc}")

    state = secrets.token_urlsafe(32)
    kv_store = request.app.state.kv_store
    await store_state_async(kv_store, state, {
        "provider_id": str(provider.id),
        "workspace_id": provider.workspace_id,
    })

    redirect_uri = str(request.base_url).rstrip("/") + "/auth/sso/callback"
    auth_url = build_auth_url(
        discovery_doc=discovery_doc,
        client_id=provider.client_id,
        redirect_uri=redirect_uri,
        scopes=provider.scopes,
        state=state,
    )
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def sso_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Handle OIDC callback: exchange code for tokens, find/create user, issue JWT."""
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        raise HTTPException(status_code=400, detail=f"OIDC error: {desc}")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter.")

    kv_store = request.app.state.kv_store
    state_data = await consume_state_async(kv_store, state)
    if state_data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter.")

    try:
        parsed_provider_id = uuid_pkg.UUID(state_data["provider_id"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=400, detail="Malformed state data.")

    result = await session.execute(
        select(OidcProvider).where(OidcProvider.id == parsed_provider_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=400, detail="SSO provider no longer exists.")
    if not provider.enabled:
        raise HTTPException(status_code=404, detail="Provider not found or disabled.")

    redirect_uri = str(request.base_url).rstrip("/") + "/auth/sso/callback"

    try:
        discovery_doc = await fetch_discovery_doc(provider.issuer)
        tokens = await exchange_code(
            code=code,
            redirect_uri=redirect_uri,
            client_id=provider.client_id,
            client_secret=provider.client_secret,
            token_endpoint=discovery_doc["token_endpoint"],
        )
        userinfo = await fetch_userinfo(
            access_token=tokens["access_token"],
            userinfo_endpoint=discovery_doc["userinfo_endpoint"],
        )
    except Exception as exc:
        logger.warning("OIDC token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="OIDC token exchange failed.")

    # Map IdP identity to LineageLens user
    sub = str(userinfo.get("sub") or userinfo.get("id") or "")
    email = str(userinfo.get("email", ""))
    if not sub:
        raise HTTPException(status_code=502, detail="OIDC userinfo missing 'sub' claim.")

    workspace_id = provider.workspace_id
    sso_username = f"sso_{sub}"[:128]

    user_result = await session.execute(
        select(UserAccount).where(
            UserAccount.username == sso_username,
            UserAccount.workspace_id == workspace_id,
        )
    )
    user = user_result.scalar_one_or_none()

    if user is None:
        user = UserAccount(
            username=sso_username,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            workspace_id=workspace_id,
            role=provider.default_role,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)
    elif not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive.")

    user.token_version = (user.token_version or 0) + 1
    refresh_jti = uuid_pkg.uuid4().hex
    user.refresh_token_jti = refresh_jti
    scopes = sorted(settings.required_scopes_set)
    token, expires_at = create_access_token(
        subject=str(user.id),
        workspace_id=workspace_id,
        scopes=scopes,
        settings=settings,
        extra_claims={"username": user.username, "role": user.role, "token_version": user.token_version},
    )
    refresh_token, _ = create_refresh_token(
        subject=str(user.id),
        workspace_id=workspace_id,
        settings=settings,
        extra_claims={"username": user.username, "role": user.role, "token_version": user.token_version, "jti": refresh_jti},
    )

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=str(user.id),
        action="sso.login",
        details={"provider": provider.name, "sub": sub, "email": email},
    )
    await session.commit()

    now = datetime.now(UTC)
    return {
        "accessToken": token,
        "refreshToken": refresh_token,
        "tokenType": "bearer",
        "expiresInSeconds": int((expires_at - now).total_seconds()),
        "expiresAtIso": expires_at.isoformat(),
        "workspaceId": workspace_id,
        "user": {
            "id": str(user.id),
            "username": user.username,
            "workspaceId": workspace_id,
            "role": user.role,
        },
    }
