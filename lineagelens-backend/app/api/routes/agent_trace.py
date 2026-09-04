"""Agent Trace import — POST /import/agent-trace.

Accepts a JSONL file (one AgentTraceDocument per line) exported by LineageLens
or any tool that speaks the cursor/agent-trace 0.1.0 specification.

One JSONL line can describe multiple files (and multiple conversations per file);
each (file, conversation) pair produces one ProvenanceRecord.  For a document
with a single file and conversation (the LineageLens export shape) this is 1:1.

Behaviour:
  - Parses each line independently; bad lines are counted as errors, not fatal.
  - Skips records whose UUID already exists in this workspace (idempotent).
  - Returns a summary: {imported, skipped, errors}.
"""

from __future__ import annotations

import json
import logging
import re
import uuid as uuid_module
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, get_current_auth_context, require_role
from app.db.models import ProvenanceRecord
from app.db.session import get_db_session
from app.schemas.agent_trace import AgentTraceDocument, AgentTraceFile, SPEC_VERSION, SCHEMA_VERSION
from app.services.agent_trace_service import agent_trace_to_provenance_payload, compute_import_hash

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent-trace"])

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_IMPORT_LINES = 50_000
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _record_uuid_for(doc_id: str, file_idx: int, conv_idx: int) -> str:
    """Stable UUID for a (document, file, conversation) triple.

    File[0] × conversation[0] uses the document's own id so that single-file
    exports round-trip with the same UUID.  Additional pairs get a deterministic
    uuid5 so re-importing the same file never creates duplicates.
    """
    if file_idx == 0 and conv_idx == 0:
        return doc_id
    return str(uuid_module.uuid5(
        uuid_module.NAMESPACE_OID,
        f"{doc_id}|{file_idx}|{conv_idx}",
    ))


def _parse_agent_trace_line(line: str, line_num: int) -> tuple["AgentTraceDocument | None", dict | None]:
    """Parse and validate one JSONL line. Returns (doc, None) or (None, error)."""
    try:
        raw_obj = json.loads(line)
    except json.JSONDecodeError as exc:
        return None, {"line": line_num, "error": f"JSON parse error: {exc}"}

    # Accept the new spec format (version: semver) and gracefully reject
    # the legacy LineageLens-internal format (schemaVersion).
    spec_ver = raw_obj.get("version", "")
    legacy_ver = raw_obj.get("schemaVersion") or raw_obj.get("schema_version", "")

    if spec_ver:
        if not _SEMVER_RE.match(str(spec_ver)):
            return None, {
                "line": line_num,
                "error": f"Invalid spec version '{spec_ver}'; expected semver like '{SPEC_VERSION}'.",
            }
    elif legacy_ver:
        # Old LineageLens-internal format — no longer supported; advise re-export.
        return None, {
            "line": line_num,
            "error": (
                f"Legacy LineageLens format (schemaVersion='{legacy_ver}') is no longer supported. "
                "Re-export with GET /export/agent-trace to get the cursor/agent-trace 0.1.0 format."
            ),
        }
    # If neither version field is present, attempt parsing anyway.

    try:
        doc = AgentTraceDocument.model_validate(raw_obj)
    except ValidationError as exc:
        return None, {"line": line_num, "error": f"Validation error: {exc.error_count()} field(s) invalid"}

    return doc, None


def _build_provenance_record_for_conversation(
    doc: "AgentTraceDocument",
    afile,
    conv,
    rec_uuid_str: str,
    fi: int,
    ci: int,
    line_num: int,
    workspace_id: str,
    now_utc: datetime,
    prev_import_hash: str | None,
) -> tuple["ProvenanceRecord | None", str | None, dict | None]:
    """Build one ProvenanceRecord for a (file, conversation) pair.

    Returns (record, record_hash, error) — record and record_hash are None
    together when the UUID is invalid (error set).
    """
    try:
        ts = datetime.fromisoformat(doc.timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        ts = now_utc

    try:
        record_uuid = uuid_module.UUID(rec_uuid_str)
    except ValueError:
        return None, None, {
            "line": line_num,
            "error": f"Invalid UUID '{rec_uuid_str}' for file[{fi}] conv[{ci}]",
        }

    # Build a per-(file, conversation) view of the document for the payload
    # builder, so agent_trace_to_provenance_payload reads the right file/conv.
    narrow_file = AgentTraceFile(path=afile.path, conversations=[conv])
    narrow = AgentTraceDocument(
        version=doc.version,
        id=rec_uuid_str,
        timestamp=doc.timestamp,
        vcs=doc.vcs,
        tool=doc.tool,
        files=[narrow_file],
        metadata=doc.metadata,
    )

    provenance_payload = agent_trace_to_provenance_payload(narrow, workspace_id, now_utc.isoformat())
    _prompt_sha256, record_hash = compute_import_hash(narrow, prev_hash=prev_import_hash)

    contributor = conv.contributor
    model_name = contributor.model_id if contributor else None

    ll_meta = doc.metadata or {}
    inserted_code = ll_meta.get("lineagelens.insertedCodePreview") or "[imported]"
    is_redacted = "lineagelens.insertedCodePreview" not in ll_meta

    record = ProvenanceRecord(
        uuid=record_uuid,
        workspace_id=workspace_id,
        file_path=afile.path,
        timestamp_iso=ts,
        inserted_code=inserted_code,
        model_name=model_name,
        is_redacted=is_redacted,
        provenance_payload=provenance_payload,
        record_hash=record_hash,
        prev_hash=prev_import_hash,
    )
    return record, record_hash, None


@router.post(
    "/import/agent-trace",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role("admin"))],
)
async def import_agent_trace(
    file: Annotated[UploadFile, File(description="JSONL file with Agent Trace documents")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Import Agent Trace documents from a JSONL file into this workspace.

    Each line must be a valid cursor/agent-trace 0.1.0 JSON document
    (i.e. the format produced by GET /export/agent-trace).
    Documents whose UUID already exists in this workspace are silently skipped,
    making the endpoint safe to call multiple times with the same file.
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

    # Pre-load existing UUIDs so we can skip duplicates without per-row DB hits.
    existing_stmt = select(ProvenanceRecord.uuid).where(
        ProvenanceRecord.workspace_id == auth.workspace_id
    )
    existing_result = await session.execute(existing_stmt)
    existing_uuids: set[str] = {str(row) for row in existing_result.scalars()}

    imported = 0
    skipped = 0
    errors: list[dict] = []
    prev_import_hash: str | None = None
    now_utc = datetime.now(tz=UTC)

    for line_num, line in enumerate(lines, start=1):
        doc, error = _parse_agent_trace_line(line, line_num)
        if error:
            errors.append(error)
            continue

        # Each (file, conversation) pair in the document becomes one ProvenanceRecord.
        for fi, afile in enumerate(doc.files):
            for ci, conv in enumerate(afile.conversations):
                rec_uuid_str = _record_uuid_for(doc.id, fi, ci)

                if rec_uuid_str in existing_uuids:
                    skipped += 1
                    continue

                record, record_hash, error = _build_provenance_record_for_conversation(
                    doc, afile, conv, rec_uuid_str, fi, ci, line_num,
                    auth.workspace_id, now_utc, prev_import_hash,
                )
                if error:
                    errors.append(error)
                    continue

                prev_import_hash = record_hash
                session.add(record)
                existing_uuids.add(rec_uuid_str)
                imported += 1

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
