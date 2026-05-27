from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def dispatch_alerts(
    session: AsyncSession,
    *,
    workspace_id: str,
    event: str,
    payload: dict,
) -> None:
    """
    Load enabled AlertConfigs for workspace_id where trigger_on contains event,
    then dispatch to each channel. Never raises — logs errors silently.

    Events: "high_risk", "critical", "policy_violation", "bulk_delete", "record_delete", "export"
    """
    try:
        await _dispatch(session, workspace_id=workspace_id, event=event, payload=payload)
    except Exception:
        logger.exception("Alert dispatch failed for workspace=%s event=%s", workspace_id, event)


async def _dispatch(session: AsyncSession, *, workspace_id: str, event: str, payload: dict) -> None:
    from sqlalchemy import select
    from app.db.models import AlertConfig

    result = await session.execute(
        select(AlertConfig).where(
            AlertConfig.workspace_id == workspace_id,
            AlertConfig.enabled.is_(True),
        )
    )
    configs = list(result.scalars().all())

    for config in configs:
        trigger_on = config.trigger_on or []
        if event not in trigger_on and "all" not in trigger_on:
            continue
        try:
            await _send_to_channel(config, event=event, payload=payload)
        except Exception:
            logger.warning("Failed to send alert via %s config %s", config.channel, config.id)


async def _send_to_channel(config, *, event: str, payload: dict) -> None:
    channel = config.channel
    cfg = config.config or {}

    message = _format_message(event, payload, config.name)

    if channel in {"slack", "teams", "webhook"}:
        webhook_url = cfg.get("webhook_url") or cfg.get("url")
        if not webhook_url:
            logger.warning("No webhook_url for %s alert config %s", channel, config.id)
            return
        await _post_webhook(webhook_url, message, channel)

    elif channel == "email":
        recipients = cfg.get("recipients") or cfg.get("email")
        smtp_host = cfg.get("smtp_host", "localhost")
        try:
            smtp_port = int(cfg.get("smtp_port", 587))
        except (TypeError, ValueError):
            smtp_port = 587
        from_addr = cfg.get("from_addr", "lineagelens@noreply.local")
        subject = f"[LineageLens Alert] {event} in workspace {config.workspace_id}"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send_email, smtp_host, smtp_port, from_addr, recipients, subject, message)


async def _post_webhook(url: str, message: dict, channel: str) -> None:
    import asyncio
    import json

    loop = asyncio.get_running_loop()

    def _do_post():
        import urllib.request

        body = json.dumps(message).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status

    try:
        await loop.run_in_executor(None, _do_post)
        logger.debug("Alert sent to %s webhook", channel)
    except Exception as exc:
        logger.warning("Webhook POST failed: %s", exc)


def _send_email(
    smtp_host: str,
    smtp_port: int,
    from_addr: str,
    recipients,
    subject: str,
    message: dict,
) -> None:
    import json
    import smtplib
    from email.mime.text import MIMEText

    if isinstance(recipients, str):
        recipients = [recipients]
    if not recipients:
        return

    # Strip any recipient that contains CR or LF to prevent email header injection.
    recipients = [r for r in recipients if isinstance(r, str) and "\r" not in r and "\n" not in r]
    if not recipients:
        logger.warning("All recipients were rejected due to invalid characters; skipping email send")
        return

    body = json.dumps(message, indent=2)
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=5) as smtp:
            smtp.sendmail(from_addr, recipients, msg.as_string())
        logger.debug("Alert email sent to %s", recipients)
    except Exception as exc:
        logger.warning("Email send failed: %s", exc)


def _format_message(event: str, payload: dict, config_name: str) -> dict:
    text = f"[LineageLens] Alert '{config_name}' triggered by event: {event}"
    details = {k: str(v)[:200] for k, v in payload.items() if k != "prompt_messages"}
    return {"text": text, "event": event, "details": details}
