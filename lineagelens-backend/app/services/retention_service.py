from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProvenanceRecord, RetentionPolicy

logger = logging.getLogger(__name__)


async def get_retention_policy(
    session: AsyncSession,
    workspace_id: str,
) -> RetentionPolicy | None:
    result = await session.execute(
        select(RetentionPolicy).where(RetentionPolicy.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def upsert_retention_policy(
    session: AsyncSession,
    workspace_id: str,
    retain_days: int,
    redact_after_days: int | None,
    enabled: bool,
) -> RetentionPolicy:
    existing = await get_retention_policy(session, workspace_id)
    if existing is None:
        policy = RetentionPolicy(
            workspace_id=workspace_id,
            retain_days=retain_days,
            redact_after_days=redact_after_days,
            enabled=enabled,
        )
        session.add(policy)
    else:
        existing.retain_days = retain_days
        existing.redact_after_days = redact_after_days
        existing.enabled = enabled
        policy = existing

    await session.flush()
    return policy


async def run_retention_cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: str | None = None,
) -> dict:
    """Delete records older than retain_days and redact records older than redact_after_days.

    If workspace_id is supplied, only that workspace is cleaned up.
    Returns {deleted: N, redacted: N}.
    """
    deleted_total = 0
    redacted_total = 0

    async with session_factory() as session:
        # Build workspace filter
        policy_stmt = select(RetentionPolicy).where(RetentionPolicy.enabled.is_(True))
        if workspace_id:
            policy_stmt = policy_stmt.where(RetentionPolicy.workspace_id == workspace_id)

        result = await session.execute(policy_stmt)
        policies = result.scalars().all()

        now = datetime.now(tz=UTC)

        for policy in policies:
            ws_id = policy.workspace_id

            # Hard delete records older than retain_days
            cutoff_delete = now - timedelta(days=policy.retain_days)
            records_to_delete_result = await session.execute(
                select(ProvenanceRecord).where(
                    and_(
                        ProvenanceRecord.workspace_id == ws_id,
                        ProvenanceRecord.timestamp_iso < cutoff_delete,
                    )
                )
            )
            records_to_delete = records_to_delete_result.scalars().all()
            for record in records_to_delete:
                session.delete(record)
            deleted_total += len(records_to_delete)

            # Soft redact records older than redact_after_days but not yet queued
            # for hard deletion — modifying a pending-delete instance in the same
            # session raises InvalidRequestError.
            if policy.redact_after_days is not None:
                cutoff_redact = now - timedelta(days=policy.redact_after_days)
                records_to_redact_result = await session.execute(
                    select(ProvenanceRecord).where(
                        and_(
                            ProvenanceRecord.workspace_id == ws_id,
                            ProvenanceRecord.timestamp_iso < cutoff_redact,
                            ProvenanceRecord.timestamp_iso >= cutoff_delete,
                            ProvenanceRecord.is_redacted.is_(False),
                        )
                    )
                )
                records_to_redact = records_to_redact_result.scalars().all()
                for record in records_to_redact:
                    record.prompt_messages = None
                    record.raw_model_response = None
                    record.surrounding_context = None
                    record.context_snapshot = None
                    record.is_redacted = True
                redacted_total += len(records_to_redact)

        await session.commit()

    logger.info(
        "Retention cleanup complete: deleted=%d redacted=%d workspace=%s",
        deleted_total,
        redacted_total,
        workspace_id or "all",
    )
    return {"deleted": deleted_total, "redacted": redacted_total}
