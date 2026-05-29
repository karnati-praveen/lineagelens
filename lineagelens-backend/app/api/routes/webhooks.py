from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context, require_admin


router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Hostnames that are always internal regardless of DNS resolution.
_BLOCKED_WEBHOOK_NAMES = frozenset({
    "localhost", "ip6-localhost", "ip6-loopback", "broadcasthost", "0.0.0.0",
})


async def _validate_webhook_url_no_ssrf(url: str) -> None:
    """Validate that a webhook URL targets a publicly routable address.

    Blocks:
    - Non-HTTP/HTTPS schemes
    - Known-internal hostnames (localhost, broadcasthost, 0.0.0.0 …)
    - Bare private / loopback / link-local / unspecified IP literals
    - Hostnames that resolve to any private or loopback IP (DNS-rebinding defence)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook URL must be a valid http:// or https:// URL.",
        )

    host = (parsed.hostname or "").lower()

    if host in _BLOCKED_WEBHOOK_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook URL must not target internal addresses.",
        )

    # Check bare IP literals first.
    try:
        addr = ipaddress.ip_address(host)
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Webhook URL must not target private, loopback, or reserved addresses.",
            )
        return  # Valid public IP literal — no DNS resolution needed.
    except ValueError:
        pass  # Not a bare IP; fall through to DNS resolution.

    # Resolve the hostname and inspect every returned address.
    try:
        loop = asyncio.get_running_loop()
        infos: list = await loop.run_in_executor(
            None, lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        )
    except (socket.gaierror, OSError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Webhook URL hostname '{host}' could not be resolved.",
        )

    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_multicast
                or addr.is_unspecified
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Webhook URL resolves to a private or reserved address.",
                )
        except ValueError:
            pass  # Malformed IP string from getaddrinfo — skip.
logger = logging.getLogger(__name__)


class WebhookConfig(BaseModel):
    id: str
    workspace_id: str
    url: str
    secret: str
    risk_threshold: int = 70
    active: bool = True
    created_at: str


class WebhookConfigPublic(BaseModel):
    """Safe view of a webhook — secret omitted."""
    id: str
    workspace_id: str
    url: str
    risk_threshold: int = 70
    active: bool = True
    created_at: str


class WebhookRegisterRequest(BaseModel):
    url: str
    secret: str
    risk_threshold: int = 70


async def _get_workspace_webhooks(app_state: object, workspace_id: str) -> list[WebhookConfig]:
    kv_store = getattr(app_state, "kv_store", None)
    if kv_store is not None:
        data = await kv_store.get(f"webhooks:{workspace_id}")
        if data is None:
            return []
        return [WebhookConfig(**item) for item in data]
    webhooks_store: dict = getattr(app_state, "webhooks", {})
    return list(webhooks_store.get(workspace_id, []))


async def _set_workspace_webhooks(
    app_state: object, workspace_id: str, webhooks: list[WebhookConfig]
) -> None:
    kv_store = getattr(app_state, "kv_store", None)
    if kv_store is not None:
        await kv_store.set(f"webhooks:{workspace_id}", [w.model_dump() for w in webhooks])
        return
    webhooks_store: dict = getattr(app_state, "webhooks", {})
    webhooks_store[workspace_id] = webhooks
    app_state.webhooks = webhooks_store  # type: ignore[attr-defined]


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_webhook(
    body: WebhookRegisterRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> WebhookConfigPublic:
    """Register a new webhook for the authenticated workspace (admin only)."""
    await _validate_webhook_url_no_ssrf(body.url)
    config = WebhookConfig(
        id=str(uuid_pkg.uuid4()),
        workspace_id=auth.workspace_id,
        url=body.url,
        secret=body.secret,
        risk_threshold=max(0, min(100, body.risk_threshold)),
        active=True,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    existing = await _get_workspace_webhooks(request.app.state, auth.workspace_id)
    existing.append(config)
    await _set_workspace_webhooks(request.app.state, auth.workspace_id, existing)
    logger.info(
        "Webhook registered: workspace=%s id=%s url=%s threshold=%d",
        auth.workspace_id,
        config.id,
        config.url,
        config.risk_threshold,
    )
    return WebhookConfigPublic(**{k: v for k, v in config.model_dump().items() if k != "secret"})


@router.get("")
async def list_webhooks(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> list[WebhookConfigPublic]:
    """List all webhooks for the authenticated workspace (secret not returned)."""
    configs = await _get_workspace_webhooks(request.app.state, auth.workspace_id)
    return [WebhookConfigPublic(**{k: v for k, v in c.model_dump().items() if k != "secret"}) for c in configs]


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_webhook(
    webhook_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_admin)],
) -> None:
    """Delete a webhook by ID (admin only)."""
    existing = await _get_workspace_webhooks(request.app.state, auth.workspace_id)
    updated = [w for w in existing if w.id != webhook_id]
    if len(updated) == len(existing):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found.",
        )
    await _set_workspace_webhooks(request.app.state, auth.workspace_id, updated)
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
    webhooks = await _get_workspace_webhooks(app_state, workspace_id)
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
