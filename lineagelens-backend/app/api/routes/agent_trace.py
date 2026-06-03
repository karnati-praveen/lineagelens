"""Agent Trace import — POST /import/agent-trace.

Accepts a JSONL file (one AgentTraceRecord per line) exported by LineageLens
or another tool that speaks the lineagelens-agent-trace/1 schema.

Behaviour:
  - Parses each line independently; bad lines are counted as errors, not fatal.
  - Skips records whose UUID already exists in this workspace (idempotent).
  - Creates a minimal ProvenanceRecord for each valid, new record.
  - Returns a summary: {imported, skipped, errors}.
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_module
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context, require_role
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.schemas.agent_trace import AgentTraceRecord, SCHEMA_VERSION
from app.services.agent_trace_service import agent_trace_to_provenance_payload, compute_import_hash

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-trace"])

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_IMPORT_LINES = 50_000


@router.post(
    "/import/agent-trace",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role("admin"))],
)
async def import_agent_trace(
    file: Annotated[UploadFile, File(description="JSONL file with Agent Trace records")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Import Agent Trace records from a JSONL file into this workspace.

    Accepts the format produced by GET /export/agent-trace.  Records whose
    UUID already exists in this workspace are silently skipped so the endpoint
    is safe to call multiple times with the same file.
    """
    raw = await file.read(_MAX_IMPORT_BYTES + 1)
    if len(raw) > _MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {_MAX_IMPORT_BYTES // (1024 * 1024)} MB limit.",
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded.")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > _MAX_IMPORT_LINES:
        raise HTTPException(
            status_code=400,
            detail=f"File contains more than {_MAX_IMPORT_LINES:,} records. Split it into smaller batches.",
        )

    # Collect all UUIDs in this workspace that already exist so we can skip
    # duplicates without hitting the DB for every single line.
    existing_stmt = select(ProvenanceRecord.uuid).where(
        ProvenanceRecord.workspace_id == auth.workspace_id
    )
    existing_result = await session.execute(existing_stmt)
    existing_uuids: set[str] = {str(row) for row in existing_result.scalars()}

    imported = 0
    skipped = 0
    errors: list[dict] = []
    prev_import_hash: str | None = None  # rolling hash chain head for this batch

    now_utc = datetime.now(tz=UTC)

    for line_num, line in enumerate(lines, start=1):
        try:
            raw_obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line": line_num, "error": f"JSON parse error: {exc}"})
            continue

        # Accept both camelCase (exported) and snake_case forms.
        schema_ver = raw_obj.get("schemaVersion") or raw_obj.get("schema_version", "")
        if schema_ver and schema_ver != SCHEMA_VERSION:
            errors.append({
                "line": line_num,
                "error": f"Unsupported schema version '{schema_ver}'. Expected '{SCHEMA_VERSION}'.",
            })
            continue

        try:
            trace = AgentTraceRecord.model_validate(raw_obj, by_alias=True)
        except ValidationError:
            # Try snake_case fallback
            try:
                trace = AgentTraceRecord.model_validate(raw_obj, by_alias=False)
            except ValidationError as exc:
                errors.append({"line": line_num, "error": f"Validation error: {exc.error_count()} field(s) invalid"})
                continue

        if trace.uuid in existing_uuids:
            skipped += 1
            continue

        # Parse timestamp; fall back to now if malformed.
        try:
            ts = datetime.fromisoformat(trace.timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = now_utc

        try:
            record_uuid = uuid_module.UUID(trace.uuid)
        except ValueError:
            errors.append({"line": line_num, "error": f"Invalid UUID '{trace.uuid}'"})
            continue

        provenance_payload = agent_trace_to_provenance_payload(
            trace, auth.workspace_id, now_utc.isoformat()
        )

        _prompt_sha256, record_hash = compute_import_hash(trace, prev_hash=prev_import_hash)

        record = ProvenanceRecord(
            uuid=record_uuid,
            workspace_id=auth.workspace_id,
            file_path=trace.file_path,
            timestamp_iso=ts,
            # Imported records have no raw code/prompts unless the exporter included them.
            inserted_code=trace.inserted_code_preview or "[imported]",
            model_name=trace.model.name,
            is_redacted=trace.inserted_code_preview is None,
            provenance_payload=provenance_payload,
            record_hash=record_hash,
            prev_hash=prev_import_hash,
        )
        prev_import_hash = record_hash
        session.add(record)
        existing_uuids.add(trace.uuid)
        imported += 1

        # Flush in batches to avoid huge in-memory transactions.
        if imported % 500 == 0:
            await session.flush()

    await session.commit()

    logger.info(
        "AGENT_TRACE_IMPORT workspace=%s imported=%d skipped=%d errors=%d",
        auth.workspace_id,
        imported,
        skipped,
        len(errors),
    )

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:50],
        "totalLines": len(lines),
        "workspaceId": auth.workspace_id,
    }
