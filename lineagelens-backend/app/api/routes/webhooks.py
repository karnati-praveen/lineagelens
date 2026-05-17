from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context, require_admin


router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


class WebhookConfig(BaseModel):
    id: str
    workspace_id: str
    url: str
    secret: str
    risk_threshold: int = 70
    active: bool = True
    created_at: str


class WebhookRegisterRequest(BaseModel):
    url: str
    secret: str
    risk_threshold: int = 70


def _get_workspace_webhooks(app_state: object, workspace_id: str) -> list[WebhookConfig]:
    webhooks_store: dict[str, list[WebhookConfig]] = getattr(app_state, "webhooks", {})
    return list(webhooks_store.get(workspace_id, []))


def _set_workspace_webhooks(
    app_state: object, workspace_id: str, webhooks: list[WebhookConfig]
) -> None:
    webhooks_store: dict[str, list[WebhookConfig]] = getattr(app_state, "webhooks", {})
    webhooks_store[workspace_id] = webhooks
    app_state.webhooks = webhooks_store  # type: ignore[attr-defined]


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_webhook(
    body: WebhookRegisterRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> WebhookConfig:
    """Register a new webhook for the authenticated workspace (admin only)."""
    parsed_url = urlparse(body.url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook URL must be a valid http:// or https:// URL.",
        )
    config = WebhookConfig(
        id=str(uuid_pkg.uuid4()),
        workspace_id=auth.workspace_id,
        url=body.url,
        secret=body.secret,
        risk_threshold=max(0, min(100, body.risk_threshold)),
        active=True,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    existing = _get_workspace_webhooks(request.app.state, auth.workspace_id)
    existing.append(config)
    _set_workspace_webhooks(request.app.state, auth.workspace_id, existing)
    logger.info(
        "Webhook registered: workspace=%s id=%s url=%s threshold=%d",
        auth.workspace_id,
        config.id,
        config.url,
        config.risk_threshold,
    )
    return config


@router.get("")
async def list_webhooks(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> list[WebhookConfig]:
    """List all webhooks for the authenticated workspace."""
    return _get_workspace_webhooks(request.app.state, auth.workspace_id)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_webhook(
    webhook_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> None:
    """Delete a webhook by ID (admin only)."""
    existing = _get_workspace_webhooks(request.app.state, auth.workspace_id)
    updated = [w for w in existing if w.id != webhook_id]
    if len(updated) == len(existing):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found.",
        )
    _set_workspace_webhooks(request.app.state, auth.workspace_id, updated)
    logger.info(
        "Webhook deleted: workspace=%s id=%s",
        auth.workspace_id,
        webhook_id,
    )


def _sign_payload(secret: str, body: bytes) -> str:
    """Return HMAC-SHA256 hex digest of body using secret."""
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


async def trigger_webhooks(
    app_state: object,
    workspace_id: str,
    record_uuid: str,
    risk_score: int,
    file_path: str,
) -> None:
    """Fire webhooks for the workspace when risk_score meets the threshold.

    Called as an asyncio background task after a successful ingest.  Errors
    are logged but never re-raised so ingest callers are not affected.
    """
    webhooks = _get_workspace_webhooks(app_state, workspace_id)
    if not webhooks:
        return

    eligible = [
        w for w in webhooks
        if w.active and risk_score >= w.risk_threshold
    ]
    if not eligible:
        return

    timestamp = datetime.now(tz=UTC).isoformat()
    event_payload = {
        "event": "high_risk_insertion",
        "uuid": record_uuid,
        "risk_score": risk_score,
        "file_path": file_path,
        "workspace_id": workspace_id,
        "timestamp": timestamp,
    }
    body_bytes = json.dumps(event_payload, separators=(",", ":")).encode("utf-8")

    async with httpx.AsyncClient(timeout=5.0) as client:
        for webhook in eligible:
            signature = _sign_payload(webhook.secret, body_bytes)
            try:
                response = await client.post(
                    webhook.url,
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Lineage-Signature": signature,
                    },
                )
                logger.info(
                    "Webhook delivered: id=%s workspace=%s status=%d",
                    webhook.id,
                    workspace_id,
                    response.status_code,
                )
            except Exception as exc:
                logger.warning(
                    "Webhook delivery failed: id=%s workspace=%s error=%s",
                    webhook.id,
                    workspace_id,
                    exc,
                )
