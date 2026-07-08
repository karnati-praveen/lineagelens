from __future__ import annotations

import logging
import os
import uuid as uuid_pkg
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.core.mode_guard import require_non_solo, require_plan
from app.core.security import (
    AuthContext,
    ensure_workspace_scope,
    get_current_auth_context,
)
from app.db.models import EvidenceCapsule
from app.db.session import get_db_session
from app.schemas.capsule import SUPPORTED_CAPSULE_VARIANTS
from app.services.capsule_service import CapsuleBuildOptions, build_capsule

router = APIRouter(
    prefix="/capsules",
    tags=["capsules"],
    dependencies=[Depends(require_non_solo), Depends(require_plan("plus"))],
)
logger = logging.getLogger(__name__)

_DEFAULT_STORAGE_DIR = "data/capsules"


def _storage_dir() -> Path:
    """Local-disk capsule storage — no paid object store required.

    Set CAPSULE_STORAGE_DIR to point at a mounted volume / network share in
    production; defaults to a directory relative to the process cwd, which is
    fine for self-hosted/solo deployments.
    """
    raw = os.environ.get("CAPSULE_STORAGE_DIR", "").strip() or _DEFAULT_STORAGE_DIR
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _serialize(row: EvidenceCapsule) -> dict:
    return {
        "publicRef": str(row.public_ref),
        "workspaceId": row.workspace_id,
        "variant": row.variant,
        "capsuleDigest": row.capsule_digest,
        "recordCount": row.record_count,
        "signature": row.signature,
        "publicKeyId": row.public_key_id,
        "dateFrom": row.date_from.isoformat() if row.date_from else None,
        "dateTo": row.date_to.isoformat() if row.date_to else None,
        "manifest": row.manifest_json,
        "createdBy": row.created_by,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("")
async def create_capsule(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
    variant: str = Query(default="full_internal"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    record_uuid: list[str] | None = Query(default=None),
) -> Response:
    """Build and download an Evidence Capsule (PART 5 #51).

    Streams the signed, content-addressed zip directly; the build is also
    indexed in evidence_capsules so the capsule itself is part of the audit
    trail. Plus/Max only.
    """
    ensure_workspace_scope(auth, workspace_id)

    if variant not in SUPPORTED_CAPSULE_VARIANTS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported capsule variant {variant!r}. Only "
                f"{sorted(SUPPORTED_CAPSULE_VARIANTS)} are built today."
            ),
        )

    options = CapsuleBuildOptions(
        variant=variant,
        date_from=date_from,
        date_to=date_to,
        record_uuids=record_uuid,
    )
    result = await build_capsule(session, workspace_id, options)

    storage_path = _storage_dir() / f"capsule-{workspace_id}-{result.capsule_digest[:16]}.zip"
    storage_path.write_bytes(result.capsule_bytes)

    row = EvidenceCapsule(
        workspace_id=workspace_id,
        variant=options.variant,
        capsule_digest=result.capsule_digest,
        manifest_json=result.manifest,
        signature=result.signature,
        public_key_id=result.public_key_id,
        record_count=result.record_count,
        date_from=options.date_from,
        date_to=options.date_to,
        storage_ref=str(storage_path),
        created_by=auth.subject,
    )
    session.add(row)
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=auth.subject,
        action="capsule.build",
        details={
            "variant": options.variant,
            "recordCount": result.record_count,
            "capsuleDigest": result.capsule_digest,
        },
    )
    await session.commit()
    await session.refresh(row)

    return Response(
        content=result.capsule_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="capsule-{result.capsule_digest[:16]}.zip"',
            "X-Capsule-Public-Ref": str(row.public_ref),
            "X-Capsule-Digest": result.capsule_digest,
        },
    )


@router.get("/{public_ref}/manifest")
async def get_capsule_manifest(
    public_ref: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    try:
        parsed_ref = uuid_pkg.UUID(public_ref)
    except ValueError:
        raise HTTPException(status_code=404, detail="Capsule not found.")

    result = await session.execute(
        select(EvidenceCapsule).where(EvidenceCapsule.public_ref == parsed_ref)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Capsule not found.")

    ensure_workspace_scope(auth, row.workspace_id)
    return _serialize(row)
