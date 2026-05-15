from __future__ import annotations

import difflib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session

router = APIRouter(tags=["diff"])
logger = logging.getLogger(__name__)


def _serialize_brief(r: ProvenanceRecord) -> dict:
    return {
        "uuid": str(r.uuid),
        "filePath": r.file_path,
        "modelName": r.model_name,
        "riskScore": r.risk_score,
        "timestampIso": r.timestamp_iso.isoformat() if r.timestamp_iso else None,
        "cursorLine": r.cursor_line,
        "isRedacted": r.is_redacted,
        "insertedCodeSnippet": (r.inserted_code or "")[:200] if not r.is_redacted else "[redacted]",
    }


@router.get("/diff/file/{file_path:path}")
async def file_evolution(
    file_path: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """
    Return all provenance records for a file path in chronological order,
    showing how AI insertions evolved over time.
    """
    result = await session.execute(
        select(ProvenanceRecord)
        .where(
            and_(
                ProvenanceRecord.workspace_id == auth.workspace_id,
                ProvenanceRecord.file_path == file_path,
            )
        )
        .order_by(ProvenanceRecord.timestamp_iso.asc())
    )
    records = list(result.scalars().all())
    return {
        "filePath": file_path,
        "records": [_serialize_brief(r) for r in records],
        "count": len(records),
    }


@router.get("/diff/compare")
async def compare_records(
    a: str,
    b: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """
    Compute a unified diff between the inserted_code of two provenance records.
    Query params: a=<uuid>, b=<uuid>
    """
    import uuid as uuid_pkg

    parsed = []
    for uid in (a, b):
        try:
            parsed.append(uuid_pkg.UUID(uid))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid UUID: {uid}")

    result = await session.execute(
        select(ProvenanceRecord).where(
            and_(
                ProvenanceRecord.workspace_id == auth.workspace_id,
                ProvenanceRecord.uuid.in_(parsed),
            )
        )
    )
    records = {str(r.uuid): r for r in result.scalars().all()}

    if str(parsed[0]) not in records:
        raise HTTPException(status_code=404, detail=f"Record {a} not found.")
    if str(parsed[1]) not in records:
        raise HTTPException(status_code=404, detail=f"Record {b} not found.")

    rec_a = records[str(parsed[0])]
    rec_b = records[str(parsed[1])]

    code_a = "" if rec_a.is_redacted else (rec_a.inserted_code or "")
    code_b = "" if rec_b.is_redacted else (rec_b.inserted_code or "")

    diff_lines = list(difflib.unified_diff(
        code_a.splitlines(keepends=True),
        code_b.splitlines(keepends=True),
        fromfile=f"{rec_a.file_path} @ {rec_a.timestamp_iso.isoformat() if rec_a.timestamp_iso else 'unknown'}",
        tofile=f"{rec_b.file_path} @ {rec_b.timestamp_iso.isoformat() if rec_b.timestamp_iso else 'unknown'}",
        lineterm="",
        n=3,
    ))

    return {
        "a": _serialize_brief(rec_a),
        "b": _serialize_brief(rec_b),
        "diff": "".join(diff_lines),
        "diffLines": diff_lines,
        "linesAdded": sum(1 for l in diff_lines if l.startswith("+")),
        "linesRemoved": sum(1 for l in diff_lines if l.startswith("-")),
    }
