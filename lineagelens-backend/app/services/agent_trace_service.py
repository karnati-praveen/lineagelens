"""Agent Trace interchange service.

Converts between ProvenanceRecord (internal) and AgentTraceDocument
(cursor/agent-trace spec v0.1.0 portable format).

Key spec-alignment decisions:
  - contributor.type uses ALL LOWERCASE: "ai", "mixed", "unknown", "human"
    (previous internal format emitted uppercase "AI" — now corrected)
  - start_line / end_line are 1-indexed per spec; cursor_line in our DB is
    0-based so we add 1 and clamp to minimum 1
  - LineageLens-specific fields (confidence, workspace_id, evidence, rich tool
    metadata, insertedCodePreview, etc.) live in metadata["lineagelens.*"] keys
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import ProvenanceRecord
from app.schemas.agent_trace import (
    SCHEMA_VERSION,
    SPEC_VERSION,
    AgentTraceContributor,
    AgentTraceConversation,
    AgentTraceDocument,
    AgentTraceFile,
    AgentTraceRange,
    AgentTraceTool,
    AgentTraceVcs,
)
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
    compute_record_hash,
)


def _extract_commit(payload: dict, source: dict) -> str | None:
    """Find a VCS commit/revision anywhere the capture pipeline might place it."""
    repo = payload.get("repository") or {}
    vcs = payload.get("vcs") or {}
    for candidate in (
        repo.get("gitCommit"),
        repo.get("commitSha"),
        repo.get("commit"),
        payload.get("gitCommit"),
        payload.get("commitSha"),
        vcs.get("revision"),
        source.get("gitCommit"),
        source.get("commitSha"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _derive_contributor_type(
    confidence_score: float | None,
    model_name: str | None,
) -> str:
    """Map confidence + model presence to a spec contributor.type string (lowercase).

    Spec enum values: "human" | "ai" | "mixed" | "unknown"

    Thresholds:
    - No model name → attribution uncertain → "unknown"
    - Confidence ≥ 0.7 → AI wrote it → "ai"
    - Confidence 0.3–0.7 → uncertain authorship → "mixed"
    - Confidence < 0.3 → too uncertain → "unknown"
    """
    if not model_name:
        return "unknown"
    if confidence_score is None:
        return "unknown"
    if confidence_score >= 0.7:
        return "ai"
    if confidence_score >= 0.3:
        return "mixed"
    return "unknown"


def record_to_agent_trace(
    record: ProvenanceRecord,
    exported_at: str | None = None,
) -> AgentTraceDocument:
    """Convert a ProvenanceRecord to an AgentTraceDocument for export."""
    payload = record.provenance_payload or {}
    ne = payload.get("normalizedEvent") or {}
    source = ne.get("source") or {}
    conf_block = ne.get("confidence") or {}
    model_block = ne.get("model") or {}
    diff_block = ne.get("diff") or {}
    evidence_raw = source.get("evidence") or payload.get("agentEvidence") or []

    conf_score: float | None = conf_block.get("value") if isinstance(conf_block, dict) else None
    conf_level: str | None = conf_block.get("level") if isinstance(conf_block, dict) else None
    contributor_type = _derive_contributor_type(conf_score, record.model_name)

    # cursor_line is 0-based in our DB; spec requires 1-indexed (minimum 1).
    raw_line = getattr(record, "cursor_line", None)
    start_line = max(1, raw_line + 1) if raw_line is not None else 1

    net_lines: int | None = diff_block.get("netAddedLines")
    if net_lines is None:
        net_lines = payload.get("netAddedLines")
    end_line = max(start_line, start_line + max(0, (net_lines or 1) - 1))

    contributor = AgentTraceContributor(
        type=contributor_type,
        # TODO:SPEC model_id should be in models.dev format e.g.
        # "anthropic/claude-opus-4-5-20251101". We use the raw model name.
        model_id=(record.model_name or model_block.get("name")) or None,
    )

    # PART 2 #15 — bind a durable content hash so the range can be re-located
    # after line moves/rebase/format drift instead of relying only on line
    # numbers. Prefer the commitment captured at hash-chain time.
    content_sha = getattr(record, "content_sha256", None) or compute_content_sha256(record.inserted_code)
    content_hash = f"sha256:{content_sha}" if content_sha else None

    conversation = AgentTraceConversation(
        # TODO:SPEC url should be a conversation lookup URL; we have only an
        # opaque conversationId, not a URL, so this is null.
        url=None,
        contributor=contributor,
        ranges=[AgentTraceRange(
            start_line=start_line,
            end_line=end_line,
            content_hash=content_hash,
        )],
    )

    tool_name = source.get("toolName")
    tool = AgentTraceTool(name=tool_name) if tool_name else None

    inserted = record.inserted_code or ""
    preview = inserted[:120].replace("\n", "↵") if inserted else None

    # All LineageLens-specific fields go into metadata["lineagelens.*"].
    metadata: dict = {
        "lineagelens.schemaVersion": SCHEMA_VERSION,
        "lineagelens.workspaceId": record.workspace_id,
        "lineagelens.exportedAt": exported_at or datetime.now(tz=UTC).isoformat(),
    }
    if conf_score is not None or conf_level is not None:
        metadata["lineagelens.confidence"] = {"score": conf_score, "level": conf_level}
    if evidence_raw:
        metadata["lineagelens.evidence"] = [e for e in evidence_raw if isinstance(e, dict)]
    if preview is not None:
        metadata["lineagelens.insertedCodePreview"] = preview
    if net_lines is not None:
        metadata["lineagelens.netAddedLines"] = net_lines
    # Rich tool info beyond name/version (not in the slim spec tool object).
    rich_tool = {k: v for k, v in {
        "adapter": source.get("adapterName"),
        "provider": source.get("provider"),
        "sessionId": source.get("sessionId"),
        "conversationId": source.get("conversationId"),
        "runId": source.get("runId"),
        "operationType": source.get("operationType"),
        "sessionKind": source.get("sessionKind"),
    }.items() if v is not None}
    if rich_tool:
        metadata["lineagelens.tool"] = rich_tool

    # PART 2 #15 — bind to the source commit when the capture pipeline supplied
    # one, so the range survives rebase/cherry-pick. Null only when truly absent.
    commit = _extract_commit(payload, source)
    vcs = AgentTraceVcs(type="git", revision=commit) if commit else None

    return AgentTraceDocument(
        version=SPEC_VERSION,
        id=str(record.uuid),
        timestamp=record.timestamp_iso.isoformat(),
        vcs=vcs,
        tool=tool,
        files=[AgentTraceFile(
            path=record.file_path,
            conversations=[conversation],
        )],
        metadata=metadata,
    )


def agent_trace_to_provenance_payload(
    doc: AgentTraceDocument,
    workspace_id: str,
    imported_at: str,
) -> dict:
    """Build the provenance_payload dict for a ProvenanceRecord created by import.

    Reads from the nested spec structure; falls back to metadata["lineagelens.*"]
    keys for LineageLens-specific fields that the spec does not carry top-level.
    """
    first_file = doc.files[0] if doc.files else None
    first_conv = first_file.conversations[0] if (first_file and first_file.conversations) else None
    contributor = first_conv.contributor if first_conv else None
    first_range = first_conv.ranges[0] if (first_conv and first_conv.ranges) else None

    meta = doc.metadata or {}
    ll_tool = meta.get("lineagelens.tool") or {}
    ll_conf = meta.get("lineagelens.confidence") or {}
    ll_evidence = meta.get("lineagelens.evidence") or []

    model_name = (contributor.model_id if contributor else None)
    net_lines = (first_range.end_line - first_range.start_line + 1) if first_range else None
    ll_net = meta.get("lineagelens.netAddedLines")

    return {
        "uuid": doc.id,
        "workspaceId": workspace_id,
        "filePath": first_file.path if first_file else None,
        "timestampIso": doc.timestamp,
        "importedAt": imported_at,
        "importSource": "agent-trace-import",
        "normalizedEvent": {
            "source": {
                "toolName": (doc.tool.name if doc.tool else None) or ll_tool.get("toolName"),
                "adapterName": ll_tool.get("adapter"),
                "provider": ll_tool.get("provider"),
                "sessionId": ll_tool.get("sessionId"),
                "conversationId": ll_tool.get("conversationId"),
                "runId": ll_tool.get("runId"),
                "operationType": ll_tool.get("operationType"),
                "sessionKind": ll_tool.get("sessionKind"),
                "evidence": ll_evidence,
            },
            "confidence": {
                "value": ll_conf.get("score"),
                "level": ll_conf.get("level"),
            },
            "model": {"name": model_name},
            "diff": {"netAddedLines": ll_net if ll_net is not None else net_lines},
        },
    }


def compute_import_hash(
    doc: AgentTraceDocument,
    prev_hash: str | None,
    file_idx: int = 0,
    conv_idx: int = 0,
) -> tuple[str | None, str]:
    """Compute (prompt_sha256, record_hash) for an imported AgentTraceDocument.

    `file_idx` / `conv_idx` select which file×conversation within the document
    to compute the hash for (usually both are 0).

    TODO: chain against the workspace's existing tail record (requires a
    SELECT FOR UPDATE query, as in provenance_service._attach_hash_chain).
    Each import batch currently starts a new sub-chain (prev_hash=None for
    the first record in the batch).
    """
    meta = doc.metadata or {}
    ll_evidence = meta.get("lineagelens.evidence") or []

    surrogate_prompt = {
        "tool": (doc.tool.model_dump(exclude_none=True) if doc.tool else {}),
        "evidence": ll_evidence,
        "import_source": "agent-trace-import",
    }
    prompt_sha256 = compute_prompt_sha256(surrogate_prompt)

    afile = doc.files[file_idx] if file_idx < len(doc.files) else None
    conv = afile.conversations[conv_idx] if (afile and conv_idx < len(afile.conversations)) else None
    contributor = conv.contributor if conv else None

    file_path = afile.path if afile else doc.id
    model_name = contributor.model_id if contributor else None
    workspace_id = meta.get("lineagelens.workspaceId") or ""

    record_hash = compute_record_hash(
        record_uuid=doc.id,
        workspace_id=workspace_id,
        file_path=file_path,
        inserted_code=meta.get("lineagelens.insertedCodePreview"),
        model_name=model_name,
        prompt_sha256=prompt_sha256,
        timestamp_iso=doc.timestamp,
        prev_hash=prev_hash,
    )
    return prompt_sha256, record_hash
