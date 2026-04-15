from datetime import UTC, datetime
import uuid as uuid_pkg

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import AuthContext
from app.db.models import ProvenanceRecord
from app.schemas.provenance import IngestRequest, SearchRequest
from app.services.ast_normalizer import normalize_ast_tokens
from app.services.embedding_service import generate_embedding
from app.services.neo4j_service import Neo4jLineageService


def infer_language_hint(file_path: str) -> str:
    lowered = (file_path or "").lower()
    if lowered.endswith(".py") or lowered.endswith(".pyi"):
        return "python"
    if lowered.endswith(".tsx") or lowered.endswith(".jsx"):
        return "tsx"
    if lowered.endswith(".ts") or lowered.endswith(".mts") or lowered.endswith(".cts"):
        return "typescript"
    return "javascript"


def extract_prompt_payload(payload: IngestRequest) -> tuple[object, str | None, dict | None, str | None]:
    prompt_messages = None
    model_name = None
    model_parameters = None
    raw_model_response = None

    if isinstance(payload.prompt, dict):
        prompt_messages = payload.prompt.get("fullMessages")
        model_name = to_nullable_string(payload.prompt.get("modelName"))
        model_parameters = payload.prompt.get("parameters")
        raw_model_response = to_nullable_string(payload.prompt.get("rawModelResponse"))

    if isinstance(payload.provenance, dict):
        prompt_messages = prompt_messages or payload.provenance.get("fullPromptMessages")
        model_name = model_name or to_nullable_string(payload.provenance.get("modelName"))
        model_parameters = model_parameters or payload.provenance.get("parameters")
        raw_model_response = raw_model_response or to_nullable_string(
            payload.provenance.get("rawModelResponse")
        )

    return prompt_messages, model_name, model_parameters, raw_model_response


def compose_embedding_text(payload: IngestRequest, prompt_messages: object) -> str:
    pieces: list[str] = []

    if isinstance(prompt_messages, str):
        pieces.append(prompt_messages)
    elif prompt_messages is not None:
        pieces.append(str(prompt_messages))

    if payload.inserted_text:
        pieces.append(payload.inserted_text)

    if isinstance(payload.surrounding_context, dict):
        before = payload.surrounding_context.get("before")
        after = payload.surrounding_context.get("after")
        if before:
            pieces.append(str(before))
        if after:
            pieces.append(str(after))

    return "\n".join(pieces)


async def ingest_provenance_event(
    session: AsyncSession,
    payload: IngestRequest,
    auth: AuthContext,
    settings: Settings,
    neo4j_service: Neo4jLineageService,
) -> ProvenanceRecord:
    workspace_id = payload.workspace_id or auth.workspace_id

    prompt_messages, model_name, model_parameters, raw_model_response = extract_prompt_payload(payload)

    timestamp = payload.timestamp_iso or datetime.now(tz=UTC)
    language_hint = infer_language_hint(payload.file_path)
    ast_tokens = normalize_ast_tokens(payload.inserted_text, language_hint)

    ast_snapshot = payload.ast_snapshot or {}
    ast_snapshot["normalizedTokens"] = ast_tokens
    ast_snapshot.setdefault("languageDetected", language_hint)
    ast_snapshot.setdefault("normalizedTokenCount", len(ast_tokens))

    embedding_text = compose_embedding_text(payload, prompt_messages)
    embedding_vector = await generate_embedding(embedding_text, settings.pgvector_dimension)

    embeddings = payload.embeddings or {}
    embeddings.setdefault("vectorDimension", settings.pgvector_dimension)
    embeddings.setdefault("vectorModel", settings.embedding_model_name)

    record = ProvenanceRecord(
        uuid=payload.id,
        workspace_id=workspace_id,
        request_uuid=payload.request_uuid,
        file_path=payload.file_path,
        file_uri=payload.file_uri,
        cursor_line=payload.cursor.line if payload.cursor else None,
        cursor_column=payload.cursor.column if payload.cursor else None,
        timestamp_iso=timestamp,
        prompt_messages=prompt_messages,
        model_name=model_name,
        model_parameters=model_parameters,
        raw_model_response=raw_model_response,
        inserted_code=payload.inserted_text,
        surrounding_context=
            payload.surrounding_context
            if isinstance(payload.surrounding_context, dict)
            else payload.surrounding_context.model_dump(by_alias=True)
            if payload.surrounding_context
            else None,
        context_snapshot=payload.context_snapshot,
        embeddings=embeddings,
        ast_snapshot=ast_snapshot,
        embedding_vector=embedding_vector,
        embedding_model=settings.embedding_model_name,
        provenance_payload=payload.model_dump(mode="json", by_alias=True),
    )

    session.add(record)
    await session.flush()

    lineage_version_id = await neo4j_service.create_initial_lineage_version(
        record_uuid=str(record.uuid),
        workspace_id=workspace_id,
        file_path=record.file_path,
        code=record.inserted_code,
        ast_tokens=ast_tokens,
        timestamp=timestamp,
    )

    record.lineage_node_id = lineage_version_id

    await session.commit()
    await session.refresh(record)

    return record


async def get_provenance_by_uuid(
    session: AsyncSession,
    record_uuid: str,
    workspace_id: str,
) -> ProvenanceRecord | None:
    try:
        parsed_uuid = uuid_pkg.UUID(record_uuid)
    except (ValueError, TypeError):
        return None

    statement = select(ProvenanceRecord).where(
        and_(
            ProvenanceRecord.uuid == parsed_uuid,
            ProvenanceRecord.workspace_id == workspace_id,
        )
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def search_provenance_records(
    session: AsyncSession,
    search: SearchRequest,
    workspace_id: str,
    settings: Settings,
) -> list[tuple[ProvenanceRecord, float | None]]:
    query_text = (search.query or search.keywords or "").strip()

    limit = search.top_k or search.limit or settings.search_default_limit
    limit = min(200, max(1, int(limit)))

    filters = [ProvenanceRecord.workspace_id == workspace_id]

    model_filter = (search.model or "").strip()
    if model_filter:
        filters.append(ProvenanceRecord.model_name == model_filter)

    file_filter = (search.file_path or search.current_file or "").strip()
    if file_filter:
        filters.append(ProvenanceRecord.file_path == file_filter)

    if search.date_from:
        filters.append(ProvenanceRecord.timestamp_iso >= search.date_from)

    if search.date_to:
        filters.append(ProvenanceRecord.timestamp_iso <= search.date_to)

    if query_text:
        query_embedding = await generate_embedding(query_text, settings.pgvector_dimension)
        distance_expr = ProvenanceRecord.embedding_vector.cosine_distance(query_embedding).label(
            "distance"
        )

        statement = (
            select(ProvenanceRecord, distance_expr)
            .where(and_(*filters))
            .where(ProvenanceRecord.embedding_vector.is_not(None))
            .order_by(distance_expr.asc())
            .limit(limit)
        )

        result = await session.execute(statement)
        rows = result.all()
        return [(row[0], to_similarity(row[1])) for row in rows]

    statement = (
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
        .limit(limit)
    )

    result = await session.execute(statement)
    records = result.scalars().all()
    return [(record, None) for record in records]


def serialize_provenance_record(
    record: ProvenanceRecord,
    score: float | None = None,
) -> dict:
    payload = dict(record.provenance_payload or {})

    payload.setdefault("id", str(record.uuid))
    payload.setdefault("uuid", str(record.uuid))
    payload.setdefault("workspaceId", record.workspace_id)
    payload.setdefault("timestampIso", record.timestamp_iso.isoformat())
    payload.setdefault("filePath", record.file_path)
    payload.setdefault("fileUri", record.file_uri)
    payload.setdefault("insertedText", record.inserted_code)
    payload.setdefault("contextSnapshot", record.context_snapshot)
    payload.setdefault("astSnapshot", record.ast_snapshot)
    payload.setdefault("embeddings", record.embeddings)
    payload.setdefault("lineageNodeId", record.lineage_node_id)

    if score is not None:
        payload["score"] = score

    return payload


def to_similarity(distance: object) -> float:
    if not isinstance(distance, (int, float)):
        return 0.0

    similarity = 1.0 - float(distance)
    return round(max(-1.0, min(1.0, similarity)), 6)


def to_nullable_string(value: object) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else None

    if value is None:
        return None

    text = str(value).strip()
    return text if text else None
