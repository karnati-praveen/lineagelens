import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CursorPosition(BaseModel):
    line: int | None = None
    column: int | None = None
    offset: int | None = None


class SurroundingContext(BaseModel):
    before: str | None = None
    after: str | None = None
    token_window: int | None = Field(default=None, alias="tokenWindow")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class IngestRequest(BaseModel):
    id: uuid.UUID = Field(alias="id")
    timestamp_iso: datetime | None = Field(default=None, alias="timestampIso")
    file_path: str = Field(alias="filePath")
    file_uri: str | None = Field(default=None, alias="fileUri")

    cursor: CursorPosition | None = None
    inserted_text: str = Field(default="", alias="insertedText")
    net_added_lines: int | None = Field(default=None, alias="netAddedLines")

    surrounding_context: SurroundingContext | dict | None = Field(
        default=None, alias="surroundingContext"
    )
    context_snapshot: dict | None = Field(default=None, alias="contextSnapshot")

    provenance: dict | None = None
    prompt: dict | None = None
    ast_snapshot: dict | None = Field(default=None, alias="astSnapshot")
    embeddings: dict | None = None

    request_uuid: uuid.UUID | None = Field(default=None, alias="requestUuid")
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class SearchRequest(BaseModel):
    query: str | None = None
    keywords: str | None = None
    model: str | None = None
    date_from: datetime | None = Field(default=None, alias="dateFrom")
    date_to: datetime | None = Field(default=None, alias="dateTo")
    file_path: str | None = Field(default=None, alias="filePath")
    current_file: str | None = Field(default=None, alias="currentFile")
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    limit: int | None = None
    top_k: int | None = Field(default=None, alias="topK")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class SearchResultItem(BaseModel):
    uuid: str
    score: float | None
    model: str | None
    timestamp_iso: str | None = Field(default=None, alias="timestampIso")
    file_path: str | None = Field(default=None, alias="filePath")
    snippet: str
    record: dict | None = None

    model_config = ConfigDict(populate_by_name=True)


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    count: int


class ProvenanceResponse(BaseModel):
    uuid: str
    record: dict


class IngestResponse(BaseModel):
    uuid: str
    workspace_id: str = Field(alias="workspaceId")
    stored: bool = True
    lineage_node_id: str | None = Field(default=None, alias="lineageNodeId")

    model_config = ConfigDict(populate_by_name=True)


class ExplainRequest(BaseModel):
    uuid: str | None = None
    record: dict | None = None
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ExplainResponse(BaseModel):
    explanation: str
    model: str
    source: str
    uuid: str | None = None
