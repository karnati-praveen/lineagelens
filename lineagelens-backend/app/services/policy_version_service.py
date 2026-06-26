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

from app.db.models import Policy, PolicyVersion

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
