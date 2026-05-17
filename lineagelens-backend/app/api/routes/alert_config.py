from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, require_role
from app.db.models import AlertConfig
from app.db.session import get_db_session

router = APIRouter(tags=["alerts"])
logger = logging.getLogger(__name__)

VALID_CHANNELS = {"slack", "teams", "email", "webhook"}
VALID_TRIGGERS = {"high_risk", "critical", "policy_violation", "bulk_delete", "record_delete", "export", "all"}


class AlertConfigCreate(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    name: str = Field(..., min_length=1, max_length=256)
    channel: str
    config: dict = Field(default_factory=dict)
    trigger_on: list[str] = Field(default_factory=list, alias="triggerOn")
    enabled: bool = True

    model_config = ConfigDict(populate_by_name=True)


class AlertConfigUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    config: dict | None = None
    trigger_on: list[str] | None = Field(default=None, alias="triggerOn")
    enabled: bool | None = None

    model_config = ConfigDict(populate_by_name=True)


def _ser(a: AlertConfig) -> dict:
    return {
        "id": str(a.id),
        "workspaceId": a.workspace_id,
        "name": a.name,
        "channel": a.channel,
        "config": {k: v for k, v in (a.config or {}).items() if k not in {"smtp_password", "api_key"}},  # redact secrets
        "triggerOn": a.trigger_on or [],
        "enabled": a.enabled,
        "createdBy": a.created_by,
        "createdAt": a.created_at.isoformat() if a.created_at else None,
    }


@router.post("/alert-configs", status_code=status.HTTP_201_CREATED)
async def create_alert_config(
    payload: AlertConfigCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    ensure_workspace_scope(auth, payload.workspace_id)
    if payload.channel not in VALID_CHANNELS:
        raise HTTPException(status_code=400, detail=f"channel must be one of {sorted(VALID_CHANNELS)}")
    invalid_triggers = set(payload.trigger_on) - VALID_TRIGGERS
    if invalid_triggers:
        raise HTTPException(status_code=400, detail=f"Invalid trigger_on values: {sorted(invalid_triggers)}")

    ac = AlertConfig(
        workspace_id=auth.workspace_id,
        name=payload.name,
        channel=payload.channel,
        config=payload.config,
        trigger_on=payload.trigger_on,
        enabled=payload.enabled,
        created_by=auth.subject,
    )
    session.add(ac)
    await session.commit()
    await session.refresh(ac)
    return _ser(ac)


@router.get("/alert-configs")
async def list_alert_configs(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    result = await session.execute(
        select(AlertConfig).where(AlertConfig.workspace_id == auth.workspace_id)
    )
    configs = list(result.scalars().all())
    return {"results": [_ser(a) for a in configs], "count": len(configs)}


@router.patch("/alert-configs/{config_id}")
async def update_alert_config(
    config_id: str,
    payload: AlertConfigUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    import uuid as uuid_pkg

    try:
        cid = uuid_pkg.UUID(config_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid config ID.")

    result = await session.execute(
        select(AlertConfig).where(AlertConfig.id == cid, AlertConfig.workspace_id == auth.workspace_id)
    )
    ac = result.scalar_one_or_none()
    if ac is None:
        raise HTTPException(status_code=404, detail="Alert config not found.")

    if payload.name is not None:
        ac.name = payload.name
    if payload.config is not None:
        ac.config = payload.config
    if payload.trigger_on is not None:
        invalid_triggers = set(payload.trigger_on) - VALID_TRIGGERS
        if invalid_triggers:
            raise HTTPException(status_code=400, detail=f"Invalid trigger_on values: {sorted(invalid_triggers)}")
        ac.trigger_on = payload.trigger_on
    if payload.enabled is not None:
        ac.enabled = payload.enabled

    await session.commit()
    await session.refresh(ac)
    return _ser(ac)


@router.delete("/alert-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_alert_config(
    config_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> None:
    import uuid as uuid_pkg

    try:
        cid = uuid_pkg.UUID(config_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid config ID.")

    result = await session.execute(
        select(AlertConfig).where(AlertConfig.id == cid, AlertConfig.workspace_id == auth.workspace_id)
    )
    ac = result.scalar_one_or_none()
    if ac is None:
        raise HTTPException(status_code=404, detail="Alert config not found.")

    await session.delete(ac)
    await session.commit()
