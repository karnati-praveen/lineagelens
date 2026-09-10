from __future__ import annotations

"""Immutable policy versioning (PART 2 #12).

Policies were mutable and deletable, so a decision made under an old policy
could not be reproduced. Every create/update/archive now appends a frozen
PolicyVersion carrying a content digest + evaluator version, and the Policy row
keeps only a pointer (current_version / current_digest) to the latest one.
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Policy, PolicyVersion, RoutingPolicy, RoutingPolicyVersion, RetentionPolicy, RetentionPolicyVersion

# Bumped whenever the policy evaluation semantics change, so a decision can be
# replayed against the evaluator that produced it.
EVALUATOR_VERSION = "policy-eval-1"


def compute_policy_digest(
    *,
    name: str,
    description: str | None,
    policy_type: str,
    config: dict,
    action: str,
) -> str:
    """Stable SHA-256 over the canonical policy content."""
    canonical = json.dumps(
        {
            "name": name,
            "description": description or "",
            "policy_type": policy_type,
            "config": config or {},
            "action": action,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def append_version(
    session: AsyncSession,
    policy: Policy,
    *,
    change_type: str,
    created_by: str | None,
) -> PolicyVersion:
    """Append an immutable version snapshot and update the policy's pointer.

    Marks the previous version superseded. Does not commit (caller owns the txn).
    """
    digest = compute_policy_digest(
        name=policy.name,
        description=policy.description,
        policy_type=policy.policy_type,
        config=policy.config,
        action=policy.action,
    )

    # Supersede the prior current version, if any.
    prev = (
        await session.execute(
            select(PolicyVersion)
            .where(
                PolicyVersion.policy_id == policy.id,
                PolicyVersion.superseded_at.is_(None),
            )
            .order_by(PolicyVersion.version.desc())
        )
    ).scalars().first()

    next_version = (prev.version + 1) if prev is not None else 1
    if prev is not None:
        from datetime import UTC, datetime
        prev.superseded_at = datetime.now(UTC)

    version = PolicyVersion(
        policy_id=policy.id,
        workspace_id=policy.workspace_id,
        version=next_version,
        change_type=change_type,
        name=policy.name,
        description=policy.description,
        policy_type=policy.policy_type,
        config=policy.config,
        action=policy.action,
        enabled=policy.enabled,
        digest=digest,
        evaluator_version=EVALUATOR_VERSION,
        created_by=created_by,
    )
    session.add(version)

    policy.current_version = next_version
    policy.current_digest = digest
    return version


async def list_versions(
    session: AsyncSession,
    policy_id,
    workspace_id: str,
) -> list[PolicyVersion]:
    result = await session.execute(
        select(PolicyVersion)
        .where(
            PolicyVersion.policy_id == policy_id,
            PolicyVersion.workspace_id == workspace_id,
        )
        .order_by(PolicyVersion.version.asc())
    )
    return list(result.scalars().all())


def compute_routing_digest(provider: str, mappings: dict) -> str:
    canonical = json.dumps({"provider": provider, "mappings": mappings}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def append_routing_version(
    session: AsyncSession,
    policy: RoutingPolicy,
    *,
    change_type: str,
    created_by: str | None,
) -> RoutingPolicyVersion:
    digest = compute_routing_digest(provider=policy.provider, mappings=policy.mappings)

    prev = (
        await session.execute(
            select(RoutingPolicyVersion)
            .where(
                RoutingPolicyVersion.policy_id == policy.id,
                RoutingPolicyVersion.superseded_at.is_(None),
            )
            .order_by(RoutingPolicyVersion.version.desc())
        )
    ).scalars().first()

    next_version = (prev.version + 1) if prev is not None else 1
    if prev is not None:
        from datetime import UTC, datetime
        prev.superseded_at = datetime.now(UTC)

    version = RoutingPolicyVersion(
        policy_id=policy.id,
        workspace_id=policy.workspace_id,
        version=next_version,
        change_type=change_type,
        provider=policy.provider,
        mappings=policy.mappings,
        enabled=policy.enabled,
        digest=digest,
        created_by=created_by,
    )
    session.add(version)
    policy.current_version = next_version
    return version


def compute_retention_digest(retain_days: int, redact_after_days: int | None) -> str:
    canonical = json.dumps({"retain_days": retain_days, "redact_after_days": redact_after_days}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def append_retention_version(
    session: AsyncSession,
    policy: RetentionPolicy,
    *,
    change_type: str,
    created_by: str | None,
) -> RetentionPolicyVersion:
    digest = compute_retention_digest(retain_days=policy.retain_days, redact_after_days=policy.redact_after_days)

    prev = (
        await session.execute(
            select(RetentionPolicyVersion)
            .where(
                RetentionPolicyVersion.policy_id == policy.id,
                RetentionPolicyVersion.superseded_at.is_(None),
            )
            .order_by(RetentionPolicyVersion.version.desc())
        )
    ).scalars().first()

    next_version = (prev.version + 1) if prev is not None else 1
    if prev is not None:
        from datetime import UTC, datetime
        prev.superseded_at = datetime.now(UTC)

    version = RetentionPolicyVersion(
        policy_id=policy.id,
        workspace_id=policy.workspace_id,
        version=next_version,
        change_type=change_type,
        retain_days=policy.retain_days,
        redact_after_days=policy.redact_after_days,
        enabled=policy.enabled,
        digest=digest,
        created_by=created_by,
    )
    session.add(version)
    policy.current_version = next_version
    return version
