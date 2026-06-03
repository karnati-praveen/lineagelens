"""Agent Trace interchange format — cursor/agent-trace spec v0.1.0.

Spec: https://github.com/cursor/agent-trace

Each exported LineageLens record maps to one AgentTraceDocument covering a
single file insertion event.  LineageLens-specific fields (confidence,
workspace_id, evidence, etc.) that have no equivalent in the vendor-neutral
spec are carried in the `metadata` dict under "lineagelens.*" keys, which is
the spec's intended extension mechanism for vendor-specific data.

Field-by-field changes from our pre-spec internal format
("lineagelens-agent-trace/1" → cursor/agent-trace 0.1.0):

  schemaVersion: "lineagelens-agent-trace/1"  → version: "0.1.0" (semver)
  uuid                                         → id
  filePath (flat)                              → files[].path (nested)
  contributorType "AI" (uppercase)             → files[].conversations[].contributor.type "ai" (lowercase)
  model.name                                   → files[].conversations[].contributor.model_id
  lineStart / lineEnd (camelCase, 0-based)     → start_line / end_line (snake_case, 1-indexed, min 1)
  workspaceId                                  → metadata["lineagelens.workspaceId"]
  confidence.score / .level                   → metadata["lineagelens.confidence"]
  evidence                                     → metadata["lineagelens.evidence"]
  insertedCodePreview                          → metadata["lineagelens.insertedCodePreview"]
  netAddedLines                                → metadata["lineagelens.netAddedLines"]
  exportedAt                                   → metadata["lineagelens.exportedAt"]
  tool.adapter/provider/sessionId/...         → metadata["lineagelens.tool"] (slim spec tool has name+version only)

Fields already present that matched (no change needed):
  timestamp (RFC 3339) — same
  tool.name — same location, same semantics
  files[].path — new name for filePath, nested

New spec fields added (populated where possible):
  vcs          — TODO:SPEC: not captured at insertion time; mapped to null
  tool.version — TODO:SPEC: not available in capture data; mapped to null
  content_hash — TODO:SPEC: LineageLens does not compute range hashes; mapped to null

TODO:SPEC The cursor/agent-trace spec is at RFC status (v0.1.0). When it
reaches stable, review whether any new top-level fields absorb our metadata keys.
"""

from __future__ import annotations

from pydantic import BaseModel

SPEC_VERSION = "0.1.0"
# Kept for metadata tagging and backward-compat import detection only.
SCHEMA_VERSION = "lineagelens-agent-trace/1"


class AgentTraceVcs(BaseModel):
    # TODO:SPEC revision is the commit SHA (git), change ID (jj), etc.
    # LineageLens does not capture this at insertion time; mapped to null.
    type: str   # "git" | "jj" | "hg" | "svn"
    revision: str


class AgentTraceTool(BaseModel):
    name: str | None = None
    # TODO:SPEC populate version from the capturing tool's version metadata
    # when available (e.g. Cursor 2.4.0, GitHub Copilot 1.x).
    version: str | None = None


class AgentTraceContributor(BaseModel):
    # Spec enum values are ALL LOWERCASE: "human" | "ai" | "mixed" | "unknown"
    # (Previous internal format used uppercase "AI" — now corrected.)
    type: str   # "human" | "ai" | "mixed" | "unknown"
    # TODO:SPEC model_id should follow models.dev convention, e.g.
    # "anthropic/claude-opus-4-5-20251101". LineageLens stores the raw model
    # name from the proxy/extension which may not be in fully qualified form.
    model_id: str | None = None   # max 250 chars per spec


class AgentTraceRelatedResource(BaseModel):
    type: str
    url: str


class AgentTraceRange(BaseModel):
    start_line: int   # 1-indexed, minimum 1
    end_line: int     # 1-indexed, minimum 1
    # TODO:SPEC content_hash format is "algorithm:hash" (e.g. "murmur3:9f2e8a1b").
    # LineageLens does not currently compute per-range content hashes; null.
    content_hash: str | None = None
    contributor: AgentTraceContributor | None = None


class AgentTraceConversation(BaseModel):
    # TODO:SPEC url should be the conversation lookup URL. LineageLens stores
    # a conversationId string, not a URL; mapped to null.
    url: str | None = None
    contributor: AgentTraceContributor | None = None
    ranges: list[AgentTraceRange]
    related: list[AgentTraceRelatedResource] | None = None


class AgentTraceFile(BaseModel):
    path: str
    conversations: list[AgentTraceConversation]


class AgentTraceDocument(BaseModel):
    """One Agent Trace document per the cursor/agent-trace spec v0.1.0."""

    version: str = SPEC_VERSION   # semver, currently "0.1.0"
    id: str                        # UUID
    timestamp: str                 # RFC 3339
    # TODO:SPEC vcs (commit SHA) is not captured at insertion time; null.
    vcs: AgentTraceVcs | None = None
    tool: AgentTraceTool | None = None
    files: list[AgentTraceFile]
    # Vendor-specific extension bag. LineageLens uses "lineagelens.*" keys.
    metadata: dict | None = None


# Backward-compat alias so import sites that reference AgentTraceRecord
# continue to work without changes.
AgentTraceRecord = AgentTraceDocument
