"""Pydantic schemas for the agent-action ledger (F4)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Allowed action_type values — matches the AgentAction.action_type column.
ActionType = Literal["shell", "file_write", "file_delete", "dependency_install", "network", "other"]


class AgentActionItem(BaseModel):
    """One discrete action the agent performed, as reported by the proxy."""

    actionType: ActionType
    toolName: str = Field(max_length=128)
    argumentsJson: dict | None = None
    occurredAt: str  # ISO-8601 datetime string
    # PART 2 #16 — authority context (optional; absence → recorded "unmandated").
    agentIdentity: str | None = Field(default=None, max_length=256)
    humanPrincipal: str | None = Field(default=None, max_length=256)
    mandateRef: str | None = Field(default=None, max_length=256)
    capability: str | None = Field(default=None, max_length=64)


class IngestAgentActionsPayload(BaseModel):
    """Payload the proxy POSTs to /agent-actions."""

    workspaceId: str = Field(max_length=128)
    sessionKey: str = Field(max_length=64)
    # SHA-256 prefix of the originating prompt context; links action → prompt → code.
    promptContextId: str | None = Field(default=None, max_length=64)
    actions: list[AgentActionItem] = Field(max_length=200)


class AgentActionResponse(BaseModel):
    """One agent action as returned by GET /agent-actions."""

    id: int
    workspaceId: str = Field(validation_alias="workspace_id")
    sessionKey: str = Field(validation_alias="session_key")
    promptContextId: str | None = Field(validation_alias="prompt_context_id")
    actionType: str = Field(validation_alias="action_type")
    toolName: str = Field(validation_alias="tool_name")
    argumentsJson: dict | None = Field(validation_alias="arguments_json")
    riskFlagsJson: dict | None = Field(validation_alias="risk_flags_json")
    agentIdentity: str | None = Field(default=None, validation_alias="agent_identity")
    humanPrincipal: str | None = Field(default=None, validation_alias="human_principal")
    mandateRef: str | None = Field(default=None, validation_alias="mandate_ref")
    capability: str | None = Field(default=None, validation_alias="capability")
    authorityState: str | None = Field(default=None, validation_alias="authority_state")
    recordHash: str | None = Field(validation_alias="record_hash")
    prevHash: str | None = Field(validation_alias="prev_hash")
    occurredAt: datetime = Field(validation_alias="occurred_at")
    createdAt: datetime = Field(validation_alias="created_at")

    model_config = {"from_attributes": True, "populate_by_name": True}


class SessionReconstructionResponse(BaseModel):
    """Full session timeline joining prompt → actions → code."""

    sessionKey: str
    workspaceId: str
    actionCount: int
    actions: list[AgentActionResponse]
    # Provenance record UUIDs for code produced by this session (may be empty
    # if the session produced no file edits captured in the ingest pipeline).
    provenanceRecordUuids: list[str]


class IngestAgentActionsResponse(BaseModel):
    recorded: int
    skipped: int
    workspaceId: str
