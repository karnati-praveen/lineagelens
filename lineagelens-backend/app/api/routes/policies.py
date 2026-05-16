from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, ensure_workspace_scope, require_role
from app.db.models import Policy
from app.db.session import get_db_session

router = APIRouter(tags=["policies"])
logger = logging.getLogger(__name__)

VALID_POLICY_TYPES = {"allowlist", "blocklist", "risk_rule", "prompt_pattern"}
VALID_ACTIONS = {"allow", "block", "flag", "alert", "log"}


class PolicyCreate(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    policy_type: str = Field(..., alias="policyType")
    config: dict = Field(default_factory=dict)
    action: str = "log"
    enabled: bool = True

    model_config = ConfigDict(populate_by_name=True)


class PolicyUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=256)
    description: str | None = None
    config: dict | None = None
    action: str | None = None
    enabled: bool | None = None

    model_config = ConfigDict(populate_by_name=True)


def _serialize_policy(p: Policy) -> dict:
    return {
        "id": str(p.id),
        "workspaceId": p.workspace_id,
        "name": p.name,
        "description": p.description,
        "policyType": p.policy_type,
        "config": p.config,
        "action": p.action,
        "enabled": p.enabled,
        "createdBy": p.created_by,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
        "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.post("/policies", status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: PolicyCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    ensure_workspace_scope(auth, payload.workspace_id)
    if payload.policy_type not in VALID_POLICY_TYPES:
        raise HTTPException(status_code=400, detail=f"policy_type must be one of {sorted(VALID_POLICY_TYPES)}")
    if payload.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {sorted(VALID_ACTIONS)}")

    policy = Policy(
        workspace_id=auth.workspace_id,
        name=payload.name,
        description=payload.description,
        policy_type=payload.policy_type,
        config=payload.config,
        action=payload.action,
        enabled=payload.enabled,
        created_by=auth.subject,
    )
    session.add(policy)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="policy.create",
        details={"name": payload.name},
    )
    await session.commit()
    await session.refresh(policy)
    return _serialize_policy(policy)


@router.get("/policies")
async def list_policies(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "reviewer"))],
) -> dict:
    result = await session.execute(
        select(Policy).where(Policy.workspace_id == auth.workspace_id).order_by(Policy.created_at.desc())
    )
    policies = list(result.scalars().all())
    return {"results": [_serialize_policy(p) for p in policies], "count": len(policies)}


@router.patch("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    payload: PolicyUpdate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    import uuid as uuid_pkg

    try:
        pid = uuid_pkg.UUID(policy_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID.")

    result = await session.execute(
        select(Policy).where(Policy.id == pid, Policy.workspace_id == auth.workspace_id)
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found.")

    if payload.name is not None:
        policy.name = payload.name
    if payload.description is not None:
        policy.description = payload.description
    if payload.config is not None:
        policy.config = payload.config
    if payload.action is not None:
        if payload.action not in VALID_ACTIONS:
            raise HTTPException(status_code=400, detail=f"action must be one of {sorted(VALID_ACTIONS)}")
        policy.action = payload.action
    if payload.enabled is not None:
        policy.enabled = payload.enabled

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="policy.update",
        target_uuid=policy_id,
    )
    await session.commit()
    await session.refresh(policy)
    return _serialize_policy(policy)


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_policy(
    policy_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> None:
    import uuid as uuid_pkg

    try:
        pid = uuid_pkg.UUID(policy_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID.")

    result = await session.execute(
        select(Policy).where(Policy.id == pid, Policy.workspace_id == auth.workspace_id)
    )
    policy = result.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found.")

    await session.delete(policy)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="policy.delete",
        target_uuid=policy_id,
    )
    await session.commit()
