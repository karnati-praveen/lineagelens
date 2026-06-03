"""Agent Trace interchange format.

Schema version: lineagelens-agent-trace/1

This is the canonical portable format for LineageLens agent attribution data.
An Agent Trace record captures *which AI tool generated a piece of code*, with
the confidence evidence chain, without requiring the raw prompt or model
response to be present.  This makes it safe to share across teams or
organisations for compliance and audit purposes.

The format is NDJSON (newline-delimited JSON): one record per line, UTF-8,
no trailing comma.  Each line is independently parseable.

NOTE on spec alignment: the "cursor/agent-trace" specification referenced in
the project brief (github.com/cursor/agent-trace) returned HTTP 404 as of
2026-06-03 and is not publicly available.  Fields marked TODO:SPEC below are
inferred from the described behaviour ("code ranges → conversations →
contributors, file/line level, human/AI/mixed/unknown") and MUST be verified
against the official spec once it is published.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "lineagelens-agent-trace/1"


class AgentToolInfo(BaseModel):
    name: str | None = None
    adapter: str | None = None
    provider: str | None = None
    session_id: str | None = Field(default=None, alias="sessionId")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    run_id: str | None = Field(default=None, alias="runId")
    operation_type: str | None = Field(default=None, alias="operationType")
    session_kind: str | None = Field(default=None, alias="sessionKind")

    model_config = {"populate_by_name": True}


class AgentModelInfo(BaseModel):
    name: str | None = None
    provider: str | None = None


class AgentConfidence(BaseModel):
    score: float | None = None
    level: str | None = None


class AgentEvidenceItem(BaseModel):
    source: str | None = None
    field: str | None = None
    value: str | None = None
    weight: float | None = None
    note: str | None = None


class AgentTraceRecord(BaseModel):
    """One exported agent trace record."""

    schema_version: Literal["lineagelens-agent-trace/1"] = Field(
        default=SCHEMA_VERSION,
        alias="schemaVersion",
    )
    uuid: str
    timestamp: str
    file_path: str = Field(alias="filePath")
    workspace_id: str = Field(alias="workspaceId")
    tool: AgentToolInfo = Field(default_factory=AgentToolInfo)
    model: AgentModelInfo = Field(default_factory=AgentModelInfo)
    confidence: AgentConfidence = Field(default_factory=AgentConfidence)
    evidence: list[AgentEvidenceItem] = Field(default_factory=list)
    net_added_lines: int | None = Field(default=None, alias="netAddedLines")
    inserted_code_preview: str | None = Field(default=None, alias="insertedCodePreview")
    exported_at: str | None = Field(default=None, alias="exportedAt")
    # TODO:SPEC verify field name "lineStart" against the official cursor/agent-trace spec.
    line_start: int | None = Field(default=None, alias="lineStart")
    # TODO:SPEC verify field name "lineEnd" against the official cursor/agent-trace spec.
    line_end: int | None = Field(default=None, alias="lineEnd")
    # TODO:SPEC verify enum values ("human","AI","mixed","unknown") against the official spec.
    contributor_type: str | None = Field(default=None, alias="contributorType")

    model_config = {"populate_by_name": True}
