from __future__ import annotations

import logging
import os
import uuid as uuid_pkg
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.security import AuthContext, ensure_workspace_scope, require_role
from app.db.models import Policy, RoutingPolicy
from app.db.session import get_db_session
from app.services.policy_version_service import append_version, list_versions

# Captured at module import time (correct for production).
# Unit tests that need a different token should patch policies._BACKEND_INGEST_TOKEN directly.
_BACKEND_INGEST_TOKEN = os.environ.get("BACKEND_INGEST_TOKEN", "")

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
        "archived": p.archived,
        "currentVersion": p.current_version,
        "currentDigest": p.current_digest,
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
    await session.flush()  # populate policy.id before snapshotting
    await append_version(session, policy, change_type="create", created_by=auth.subject)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="policy.create",
        details={"name": payload.name, "version": policy.current_version},
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

    # Append an immutable new version rather than silently overwriting history.
    await append_version(session, policy, change_type="update", created_by=auth.subject)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="policy.update",
        target_uuid=policy_id,
        details={"version": policy.current_version, "digest": policy.current_digest},
    )
    await session.commit()
    await session.refresh(policy)
    return _serialize_policy(policy)


@router.get("/policies/{policy_id}/versions")
async def get_policy_versions(
    policy_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin", "reviewer"))],
) -> dict:
    """Return the immutable version history of a policy (PART 2 #12)."""
    try:
        pid = uuid_pkg.UUID(policy_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid policy ID.")

    versions = await list_versions(session, pid, auth.workspace_id)
    return {
        "results": [
            {
                "version": v.version,
                "changeType": v.change_type,
                "name": v.name,
                "description": v.description,
                "policyType": v.policy_type,
                "config": v.config,
                "action": v.action,
                "enabled": v.enabled,
                "digest": v.digest,
                "evaluatorVersion": v.evaluator_version,
                "createdBy": v.created_by,
                "createdAt": v.created_at.isoformat() if v.created_at else None,
                "supersededAt": v.superseded_at.isoformat() if v.superseded_at else None,
            }
            for v in versions
        ],
        "count": len(versions),
    }


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_policy(
    policy_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> None:
    """Archive a policy. Non-destructive: the row and its immutable version
    history are retained so past decisions remain reproducible (PART 2 #12)."""
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

    policy.enabled = False
    policy.archived = True
    await append_version(session, policy, change_type="archive", created_by=auth.subject)
    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action="policy.delete",
        target_uuid=policy_id,
        details={"archived": True, "version": policy.current_version},
    )
    await session.commit()


# ── Dynamic Routing Policy ────────────────────────────────────────────────────

VALID_ROUTING_PROVIDERS = {"anthropic", "openai", "gemini"}

# Sensible default tier → model mappings for each supported provider.
# Used when a policy is created/updated with an empty mappings dict so that
# users never need to look up model names manually.  Override by supplying
# an explicit mappings object in the PUT body.
PROVIDER_DEFAULT_MAPPINGS: dict[str, dict[str, str]] = {
    "anthropic": {
        "simple":   "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-4-6",
        "complex":  "claude-opus-4-7",
    },
    "openai": {
        "simple":   "gpt-4o-mini",
        "standard": "gpt-4o-mini",
        "complex":  "gpt-4o",
    },
    "gemini": {
        "simple":   "gemini-2.5-flash",
        "standard": "gemini-2.5-flash",
        "complex":  "gemini-2.5-pro",
    },
}


class RoutingPolicyUpsert(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    provider: str
    # When omitted or left empty the endpoint fills in PROVIDER_DEFAULT_MAPPINGS
    # for the chosen provider, so callers only need to supply {workspaceId,
    # provider, enabled: true} to get a working routing policy immediately.
    mappings: dict = Field(default_factory=dict)
    enabled: bool = False

    model_config = ConfigDict(populate_by_name=True)


def _serialize_routing_policy(p: RoutingPolicy) -> dict:
    return {
        "id": str(p.id),
        "workspaceId": p.workspace_id,
        "provider": p.provider,
        "mappings": p.mappings,
        "enabled": p.enabled,
        "createdBy": p.created_by,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
        "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/policies/routing")
async def get_routing_policies(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    """Return all dynamic routing policies for the authenticated workspace."""
    result = await session.execute(
        select(RoutingPolicy)
        .where(RoutingPolicy.workspace_id == auth.workspace_id)
        .order_by(RoutingPolicy.created_at.desc())
    )
    policies = list(result.scalars().all())
    return {"results": [_serialize_routing_policy(p) for p in policies], "count": len(policies)}


@router.put("/policies/routing", status_code=status.HTTP_200_OK)
async def upsert_routing_policy(
    payload: RoutingPolicyUpsert,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    """Create or update the routing policy for a given provider in this workspace.

    Uses an upsert pattern: if a policy already exists for this
    (workspace_id, provider) pair it is updated in place; otherwise a new row
    is inserted.
    """
    ensure_workspace_scope(auth, payload.workspace_id)

    if payload.provider not in VALID_ROUTING_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of {sorted(VALID_ROUTING_PROVIDERS)}",
        )

    # Auto-fill provider defaults when caller omits or empties mappings.
    effective_mappings = payload.mappings or PROVIDER_DEFAULT_MAPPINGS.get(payload.provider, {})

    result = await session.execute(
        select(RoutingPolicy).where(
            RoutingPolicy.workspace_id == auth.workspace_id,
            RoutingPolicy.provider == payload.provider,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.mappings = effective_mappings
        existing.enabled = payload.enabled
        policy = existing
        action = "routing_policy.update"
    else:
        policy = RoutingPolicy(
            workspace_id=auth.workspace_id,
            provider=payload.provider,
            mappings=effective_mappings,
            enabled=payload.enabled,
            created_by=auth.subject,
        )
        session.add(policy)
        action = "routing_policy.create"

    await log_audit_event(
        session,
        workspace_id=auth.workspace_id,
        user_id=auth.subject,
        action=action,
        details={"provider": payload.provider, "enabled": payload.enabled},
    )
    await session.commit()
    await session.refresh(policy)
    return _serialize_routing_policy(policy)


@router.get("/policies/routing/defaults")
async def get_routing_defaults(
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict:
    """Return the built-in default tier → model mappings for every provider.

    Useful for populating a UI picker: the client can show these defaults
    and let the admin override individual tiers before saving.
    """
    return {"defaults": PROVIDER_DEFAULT_MAPPINGS}


@router.get("/policies/routing/internal")
async def get_routing_policies_internal(
    x_backend_token: Annotated[str | None, Header()] = None,
    workspace_id: str | None = None,
    provider: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Internal endpoint used by the proxy to load routing policies.

    Protected by the X-Backend-Token header (== BACKEND_INGEST_TOKEN env var).
    Returns all enabled routing policies; optionally filtered by workspace_id
    and/or provider query parameters.

    This endpoint is intentionally NOT under JWT auth — the proxy uses the
    same shared ingest token for both /ingest and this route.
    """
    if not _BACKEND_INGEST_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Routing policy internal endpoint is not available (BACKEND_INGEST_TOKEN not configured).",
        )
    if x_backend_token != _BACKEND_INGEST_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid backend token.")

    query = select(RoutingPolicy).where(RoutingPolicy.enabled.is_(True))
    if workspace_id:
        query = query.where(RoutingPolicy.workspace_id == workspace_id)
    if provider:
        query = query.where(RoutingPolicy.provider == provider)

    result = await session.execute(query)
    policies = list(result.scalars().all())
    return {"policies": [_serialize_routing_policy(p) for p in policies]}
