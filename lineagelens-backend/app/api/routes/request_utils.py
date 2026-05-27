"""Shared request utilities used by multiple route modules.

Extracted to avoid duplication between comments.py and tags.py.
"""
from __future__ import annotations

import uuid as uuid_pkg
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status
from sqlalchemy import and_, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def get_client_ip(request: Request) -> str | None:
    """Return the client's IP address, preferring X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def assert_record_exists(
    session: "AsyncSession",
    record_uuid: str,
    workspace_id: str,
    access_filters: list[object] | None = None,
) -> None:
    """Raise 404 if the provenance record does not exist in the workspace."""
    from app.db.models import ProvenanceRecord

    try:
        parsed_uuid = uuid_pkg.UUID(record_uuid)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )

    result = await session.execute(
        select(ProvenanceRecord.uuid).where(
            and_(
                ProvenanceRecord.uuid == parsed_uuid,
                ProvenanceRecord.workspace_id == workspace_id,
                *([] if access_filters is None else access_filters),
            )
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found for this workspace.",
        )
