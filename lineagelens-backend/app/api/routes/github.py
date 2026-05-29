from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.encryption import decrypt_field, encrypt_field
from app.core.security import AuthContext, get_current_auth_context, require_admin
from app.db.models import ApiKey, GithubIntegration
from app.db.session import get_db_session
from app.services.risk_service import compute_risk_score

router = APIRouter(prefix="/github", tags=["github"])
logger = logging.getLogger(__name__)


class GithubConfigUpdate(BaseModel):
    risk_threshold: int = Field(default=70, ge=0, le=100, alias="riskThreshold")
    block_on_high_risk: bool = Field(default=True, alias="blockOnHighRisk")
    allowed_repos: list[str] = Field(default_factory=list, alias="allowedRepos")
    token: str | None = None
    webhook_secret: str | None = Field(default=None, alias="webhookSecret")

    model_config = ConfigDict(populate_by_name=True)


class CodeCheckRequest(BaseModel):
    file_path: str = Field(alias="filePath")
    code: str
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


async def _authenticate_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    session: AsyncSession = Depends(get_db_session),
) -> tuple[str, str]:
    """Authenticate via X-API-Key header. Returns (workspace_id, user_id)."""
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required for CI/CD authentication.",
        )
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active.is_(True))
    )
    ak = result.scalar_one_or_none()
    if ak is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key.")
    if ak.expires_at and ak.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key has expired.")
    ak.last_used_at = datetime.now(UTC)
    await session.commit()
    return ak.workspace_id, ak.user_id


@router.post("/check")
async def check_code_risk(
    payload: CodeCheckRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    api_key_ctx: Annotated[tuple[str, str], Depends(_authenticate_api_key)],
) -> dict:
    """Evaluate risk of a code block for GitHub/GitLab CI gates. Authenticate with X-API-Key header."""
    workspace_id, user_id = api_key_ctx

    result = await session.execute(
        select(GithubIntegration).where(GithubIntegration.workspace_id == workspace_id)
    )
    config = result.scalar_one_or_none()
    threshold = config.risk_threshold if config else 70
    block_on_high = config.block_on_high_risk if config else True

    risk_score, reasons = compute_risk_score(
        inserted_code=payload.code,
        file_path=payload.file_path,
    )

    passed = not block_on_high or risk_score < threshold

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="github.check",
        details={
            "file_path": payload.file_path,
            "risk_score": risk_score,
            "threshold": threshold,
            "passed": passed,
        },
    )
    await session.commit()

    return {
        "riskScore": risk_score,
        "threshold": threshold,
        "passed": passed,
        "reasons": reasons,
        "filePath": payload.file_path,
    }


@router.get("/config")
async def get_github_config(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Get GitHub/GitLab integration configuration for the workspace."""
    result = await session.execute(
        select(GithubIntegration).where(GithubIntegration.workspace_id == auth.workspace_id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        return {
            "configured": False,
            "riskThreshold": 70,
            "blockOnHighRisk": True,
            "allowedRepos": [],
        }
    return {
        "configured": True,
        "riskThreshold": config.risk_threshold,
        "blockOnHighRisk": config.block_on_high_risk,
        "allowedRepos": config.allowed_repos,
        "updatedAt": config.updated_at.isoformat(),
    }


@router.put("/config")
async def update_github_config(
    payload: GithubConfigUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> dict:
    """Update GitHub/GitLab integration configuration (admin only)."""
    result = await session.execute(
        select(GithubIntegration).where(GithubIntegration.workspace_id == auth.workspace_id)
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = GithubIntegration(workspace_id=auth.workspace_id)
        session.add(config)

    config.risk_threshold = payload.risk_threshold
    config.block_on_high_risk = payload.block_on_high_risk
    config.allowed_repos = payload.allowed_repos
    if payload.token is not None:
        config.token = encrypt_field(payload.token)
    if payload.webhook_secret is not None:
        config.webhook_secret = encrypt_field(payload.webhook_secret)

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="github.config.update",
        details={"risk_threshold": payload.risk_threshold, "block_on_high_risk": payload.block_on_high_risk},
    )
    await session.commit()
    await session.refresh(config)

    return {
        "configured": True,
        "riskThreshold": config.risk_threshold,
        "blockOnHighRisk": config.block_on_high_risk,
        "allowedRepos": config.allowed_repos,
    }


@router.post("/webhook")
async def receive_github_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    """Receive GitHub/GitLab webhook events for automated risk scanning."""
    workspace_id = request.headers.get("X-LineageLens-Workspace")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="X-LineageLens-Workspace header required.")

    result = await session.execute(
        select(GithubIntegration).where(GithubIntegration.workspace_id == workspace_id)
    )
    config = result.scalar_one_or_none()

    if config is None or not (config.webhook_secret or "").strip():
        raise HTTPException(status_code=403, detail="GitHub webhooks are not configured for this workspace.")

    body = await request.body()

    raw_secret = decrypt_field(config.webhook_secret or "")
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    expected_sig = "sha256=" + hmac.new(
        raw_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig_header, expected_sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event_type = request.headers.get("X-GitHub-Event", "unknown")

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=None,
        action=f"github.webhook.{event_type}",
        details={
            "action": event.get("action"),
            "repo": (event.get("repository") or {}).get("full_name"),
        },
    )
    await session.commit()

    return {"received": True, "event": event_type}
