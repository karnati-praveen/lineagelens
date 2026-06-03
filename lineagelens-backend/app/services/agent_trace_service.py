"""Agent Trace interchange service.

Converts between ProvenanceRecord (internal) and AgentTraceRecord (portable),
and builds provenance_payload dicts for imported records.

NOTE: The "cursor/agent-trace" specification (github.com/cursor/agent-trace)
was not publicly available as of 2026-06-03 (HTTP 404).  Fields and mappings
marked TODO:SPEC are inferred from the described behaviour
("code ranges → conversations → contributors, file/line level,
human/AI/mixed/unknown") and must be verified when the spec is published.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db.models import ProvenanceRecord
from app.schemas.agent_trace import (
    SCHEMA_VERSION,
    AgentConfidence,
    AgentEvidenceItem,
    AgentModelInfo,
    AgentToolInfo,
    AgentTraceRecord,
)
from app.services.integrity_service import compute_prompt_sha256, compute_record_hash


def _derive_contributor_type(confidence_score: float | None, model_name: str | None) -> str:
    """Map confidence score + model presence to a contributor_type string.

    Mapping rationale:
    - No model name → attribution is uncertain → "unknown"
    - High confidence (>=0.7) → the AI wrote it → "AI"
    - Mid confidence (0.3-0.7) → uncertain authorship → "mixed"
    - Low confidence (<0.3) → too uncertain to attribute → "unknown"

    TODO:SPEC verify the "human"/"AI"/"mixed"/"unknown" enum values and the
    recommended mapping thresholds against the official cursor/agent-trace spec.
    """
    if not model_name:
        return "unknown"
    if confidence_score is None:
        return "unknown"
    if confidence_score >= 0.7:
        return "AI"
    if confidence_score >= 0.3:
        return "mixed"
    return "unknown"


def record_to_agent_trace(
    record: ProvenanceRecord,
    exported_at: str | None = None,
) -> AgentTraceRecord:
    """Convert a ProvenanceRecord to an AgentTraceRecord for export."""
    payload = record.provenance_payload or {}
    ne = payload.get("normalizedEvent") or {}
    source = ne.get("source") or {}
    conf_block = ne.get("confidence") or {}
    model_block = ne.get("model") or {}
    diff_block = ne.get("diff") or {}
    evidence_raw = source.get("evidence") or payload.get("agentEvidence") or []

    tool = AgentToolInfo(
        name=source.get("toolName"),
        adapter=source.get("adapterName"),
        provider=source.get("provider"),
        sessionId=source.get("sessionId"),
        conversationId=source.get("conversationId"),
        runId=source.get("runId"),
        operationType=source.get("operationType"),
        sessionKind=source.get("sessionKind"),
    )

    model = AgentModelInfo(
        name=record.model_name or model_block.get("name"),
        provider=source.get("provider"),
    )

    conf_score: float | None = conf_block.get("value") if isinstance(conf_block, dict) else None
    confidence = AgentConfidence(
        score=conf_score,
        level=conf_block.get("level") if isinstance(conf_block, dict) else None,
    )

    evidence = [
        AgentEvidenceItem(
            source=e.get("source"),
            field=e.get("field"),
            value=e.get("value"),
            weight=e.get("weight"),
            note=e.get("note"),
        )
        for e in evidence_raw
        if isinstance(e, dict)
    ]

    net_lines = diff_block.get("netAddedLines")
    if net_lines is None:
        net_lines = payload.get("netAddedLines")

    inserted = record.inserted_code or ""
    preview = inserted[:120].replace("\n", "↵") if inserted else None

    # TODO:SPEC cursor_line is 0-based in our model; verify whether the spec
    # expects 0-based or 1-based line numbers for lineStart/lineEnd.
    line_start: int | None = getattr(record, "cursor_line", None)

    contributor_type = _derive_contributor_type(conf_score, record.model_name)

    return AgentTraceRecord(
        schemaVersion=SCHEMA_VERSION,
        uuid=str(record.uuid),
        timestamp=record.timestamp_iso.isoformat(),
        filePath=record.file_path,
        workspaceId=record.workspace_id,
        tool=tool,
        model=model,
        confidence=confidence,
        evidence=evidence,
        netAddedLines=net_lines,
        insertedCodePreview=preview,
        exportedAt=exported_at or datetime.now(tz=UTC).isoformat(),
        lineStart=line_start,
        contributorType=contributor_type,
    )


def agent_trace_to_provenance_payload(
    trace: AgentTraceRecord,
    workspace_id: str,
    imported_at: str,
) -> dict:
    """Build the provenance_payload dict for a ProvenanceRecord created by import."""
    tool = trace.tool
    model = trace.model
    conf = trace.confidence
    return {
        "uuid": trace.uuid,
        "workspaceId": workspace_id,
        "filePath": trace.file_path,
        "timestampIso": trace.timestamp,
        "importedAt": imported_at,
        "importSource": "agent-trace-import",
        "normalizedEvent": {
            "source": {
                "toolName": tool.name,
                "adapterName": tool.adapter,
                "provider": tool.provider,
                "sessionId": tool.session_id,
                "conversationId": tool.conversation_id,
                "runId": tool.run_id,
                "operationType": tool.operation_type,
                "sessionKind": tool.session_kind,
            },
            "confidence": {
                "value": conf.score,
                "level": conf.level,
            },
            "model": {
                "name": model.name,
            },
            "diff": {
                "netAddedLines": trace.net_added_lines,
            },
            "evidence": [e.model_dump(exclude_none=True) for e in trace.evidence],
        },
    }


def compute_import_hash(
    trace: AgentTraceRecord,
    prev_hash: str | None,
) -> tuple[str | None, str]:
    """Compute (prompt_sha256, record_hash) for an imported AgentTraceRecord.

    Uses the trace's evidence + tool data as a surrogate "prompt fingerprint"
    so imported records participate in the hash chain.

    TODO: chain against the workspace's existing tail record (requires a
    SELECT FOR UPDATE query, as in provenance_service._attach_hash_chain).
    Currently each import batch starts a new sub-chain (prev_hash=None for
    the first record in the batch).
    """
    surrogate_prompt = {
        "tool": trace.tool.model_dump(exclude_none=True),
        "evidence": [e.model_dump(exclude_none=True) for e in trace.evidence],
        "import_source": "agent-trace-import",
    }
    prompt_sha256 = compute_prompt_sha256(surrogate_prompt)

    record_hash = compute_record_hash(
        record_uuid=trace.uuid,
        workspace_id=trace.workspace_id,
        file_path=trace.file_path,
        inserted_code=trace.inserted_code_preview,
        model_name=trace.model.name,
        prompt_sha256=prompt_sha256,
        timestamp_iso=trace.timestamp,
        prev_hash=prev_hash,
    )
    return prompt_sha256, record_hash
