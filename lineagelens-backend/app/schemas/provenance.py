import base64
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    offset: int = Field(default=0, ge=0)
    cursor: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @field_validator("query", "keywords", mode="before")
    @classmethod
    def validate_query_length(cls, value: object) -> object:
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("Search query must not exceed 500 characters.")
        return value

    @field_validator("limit", "top_k", mode="before")
    @classmethod
    def validate_limit(cls, value: object) -> object:
        if isinstance(value, int) and value > 200:
            raise ValueError("Result limit must not exceed 200.")
        return value


class SearchResultItem(BaseModel):
    uuid: str
    score: float | None
    model: str | None
    timestamp_iso: str | None = Field(default=None, alias="timestampIso")
    file_path: str | None = Field(default=None, alias="filePath")
    snippet: str
    record: dict | None = None

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


def encode_cursor(timestamp_iso: str, record_uuid: str) -> str:
    """Encode a stable pagination cursor from a record's timestamp and UUID."""
    raw = f"{timestamp_iso}\n{record_uuid}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[str, str] | None:
    """Decode a cursor back to (timestamp_iso, uuid). Returns None on invalid input."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp_iso, record_uuid = raw.split("\n", 1)
        return timestamp_iso, record_uuid
    except Exception:
        return None


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    count: int
    total: int | None = None
    offset: int = 0
    next_cursor: str | None = Field(default=None, alias="nextCursor")
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


class ProvenanceResponse(BaseModel):
    uuid: str
    record: dict


class IngestResponse(BaseModel):
    uuid: str
    workspace_id: str = Field(alias="workspaceId")
    stored: bool = True
    lineage_node_id: str | None = Field(default=None, alias="lineageNodeId")
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


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
