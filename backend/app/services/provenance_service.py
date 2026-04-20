from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import uuid as uuid_pkg
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import AuthContext
from app.db.models import ProvenanceRecord
from app.schemas.provenance import SearchRequest
from app.services.ast_normalizer import normalize_ast_tokens
from app.services.embedding_service import generate_embedding
from app.services.ingest_normalizer import NormalizedIngestPayload
from app.services.neo4j_service import Neo4jLineageService


@dataclass(slots=True)
class IngestOutcome:
    record: ProvenanceRecord
    warnings: list[str]


def infer_language_hint(file_path: str) -> str:
    lowered = (file_path or "").lower()
    if lowered.endswith(".py") or lowered.endswith(".pyi"):
        return "python"
    if lowered.endswith(".tsx") or lowered.endswith(".jsx"):
        return "tsx"
    if lowered.endswith(".ts") or lowered.endswith(".mts") or lowered.endswith(".cts"):
        return "typescript"
    return "javascript"


def compose_embedding_text(payload: NormalizedIngestPayload) -> str:
    pieces: list[str] = []

    if isinstance(payload.prompt_messages, str):
        pieces.append(payload.prompt_messages)
    elif payload.prompt_messages is not None:
        pieces.append(str(payload.prompt_messages))

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
    payload: NormalizedIngestPayload,
    auth: AuthContext,
    settings: Settings,
    neo4j_service: Neo4jLineageService | None,
) -> IngestOutcome:
    warnings = list(payload.warnings)

    existing_record = await find_existing_ingest_record(
        session=session,
        workspace_id=payload.workspace_id or auth.workspace_id,
        record_uuid=payload.record_uuid,
        request_uuid=payload.request_uuid,
    )
    if existing_record is not None:
                warnings.append("Duplicate ingest detected; existing record returned.")
                return IngestOutcome(record=existing_record, warnings=warnings)

    timestamp = payload.timestamp_iso
    language_hint = infer_language_hint(payload.file_path)
    ast_tokens = normalize_ast_tokens(payload.inserted_text, language_hint)

    ast_snapshot = dict(payload.ast_snapshot or {})
    ast_snapshot["normalizedTokens"] = ast_tokens
    ast_snapshot.setdefault("languageDetected", language_hint)
    ast_snapshot.setdefault("normalizedTokenCount", len(ast_tokens))

    embedding_text = compose_embedding_text(payload)
    embedding_vector = await generate_embedding(embedding_text, settings.pgvector_dimension, settings)

    embeddings = dict(payload.embeddings or {})
    embeddings.setdefault("vectorDimension", settings.pgvector_dimension)
    embeddings.setdefault("vectorModel", settings.embedding_model_name)

    try:
        stored_user_id = uuid_pkg.UUID(auth.subject)
    except (ValueError, AttributeError):
        stored_user_id = None

    record = ProvenanceRecord(
        uuid=payload.record_uuid,
        workspace_id=payload.workspace_id or auth.workspace_id,
        user_id=stored_user_id,
        request_uuid=payload.request_uuid,
        file_path=payload.file_path,
        file_uri=payload.file_uri,
        cursor_line=payload.cursor_line,
        cursor_column=payload.cursor_column,
        timestamp_iso=timestamp,
        prompt_messages=payload.prompt_messages,
        model_name=payload.model_name,
        model_parameters=payload.model_parameters,
        raw_model_response=payload.raw_model_response,
        inserted_code=payload.inserted_text,
        surrounding_context=payload.surrounding_context,
        context_snapshot=payload.context_snapshot,
        embeddings=embeddings,
        ast_snapshot=ast_snapshot,
        embedding_vector=embedding_vector,
        embedding_model=settings.embedding_model_name,
        provenance_payload=payload.provenance_payload,
    )

    try:
        session.add(record)
        await session.flush()

        if neo4j_service is None:
            warnings.append("Neo4j lineage is disabled; record stored without graph lineage.")
        else:
            try:
                lineage_version_id = await neo4j_service.create_initial_lineage_version(
                    record_uuid=str(record.uuid),
                    workspace_id=record.workspace_id,
                    file_path=record.file_path,
                    code=record.inserted_code,
                    ast_tokens=ast_tokens,
                    timestamp=timestamp,
                )
                record.lineage_node_id = lineage_version_id
            except Exception as error:
                if settings.lineage_strict_mode:
                    await session.rollback()
                    raise

                warnings.append(
                    "Neo4j lineage is unavailable; record stored without graph lineage."
                )
                warnings.append(f"Neo4j lineage creation failed: {error}")

        await session.commit()
        await session.refresh(record)
    except IntegrityError:
        await session.rollback()

        existing_record = await find_existing_ingest_record(
            session=session,
            workspace_id=record.workspace_id,
            record_uuid=record.uuid,
            request_uuid=record.request_uuid,
        )
        if existing_record is not None:
            warnings.append("Duplicate ingest detected; existing record returned.")
            return IngestOutcome(record=existing_record, warnings=warnings)

        raise

    return IngestOutcome(record=record, warnings=warnings)


async def find_existing_ingest_record(
    session: AsyncSession,
    *,
    workspace_id: str,
    record_uuid: uuid_pkg.UUID,
    request_uuid: uuid_pkg.UUID | None,
) -> ProvenanceRecord | None:
    lookup_clauses = [ProvenanceRecord.uuid == record_uuid]

    if request_uuid is not None:
        lookup_clauses.append(ProvenanceRecord.request_uuid == request_uuid)

    statement = select(ProvenanceRecord).where(
        and_(
            ProvenanceRecord.workspace_id == workspace_id,
            or_(*lookup_clauses),
        )
    )

    result = await session.execute(statement)
    return result.scalar_one_or_none()


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
) -> tuple[list[tuple[ProvenanceRecord, float | None]], list[str]]:
    query_text = (search.query or search.keywords or "").strip()

    limit = search.top_k or search.limit or settings.search_default_limit
    limit = min(200, max(1, int(limit)))

    filters = build_workspace_record_filters(search, workspace_id)

    if query_text and settings.vector_search_enabled:
        query_embedding = await generate_embedding(query_text, settings.pgvector_dimension, settings)
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
        return [(row[0], to_similarity(row[1])) for row in rows], []

    if query_text:
        keyword_scan_limit = min(limit * 10, 1000)
        statement = (
            select(ProvenanceRecord)
            .where(and_(*filters))
            .order_by(desc(ProvenanceRecord.timestamp_iso))
            .limit(keyword_scan_limit)
        )

        result = await session.execute(statement)
        records = result.scalars().all()

        tokens = tokenize_query(query_text)
        rows_with_scores: list[tuple[ProvenanceRecord, float | None]] = []
        for record in records:
            score = score_keyword_match(record, tokens)
            if score <= 0:
                continue
            rows_with_scores.append((record, score))

        rows_with_scores.sort(
            key=lambda item: (
                item[1] if isinstance(item[1], (int, float)) else 0.0,
                item[0].timestamp_iso,
            ),
            reverse=True,
        )

        warnings = ["Vector search is disabled; using keyword fallback search."]
        return rows_with_scores[:limit], warnings

    statement = (
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
        .limit(limit)
    )

    result = await session.execute(statement)
    records = result.scalars().all()
    return [(record, None) for record in records], []


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


def tokenize_query(query_text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_@.-]+", query_text.lower())
    return [token for token in tokens if token]


def score_keyword_match(record: ProvenanceRecord, tokens: list[str]) -> float:
    if not tokens:
        return 0.0

    payload_blob = json.dumps(record.provenance_payload or {}, sort_keys=True, default=str).lower()
    file_path = (record.file_path or "").lower()
    inserted_code = (record.inserted_code or "").lower()
    prompt_blob = json.dumps(record.prompt_messages or {}, sort_keys=True, default=str).lower()
    model_name = (record.model_name or "").lower()

    total_score = 0.0
    max_score = 0.0

    for token in tokens:
        token_score = 0.0
        if token in file_path:
            token_score += 3.0
        if token in inserted_code:
            token_score += 2.0
        if token in prompt_blob:
            token_score += 2.0
        if token in model_name:
            token_score += 2.0
        if token in payload_blob:
            token_score += 1.0

        total_score += token_score
        max_score += 10.0

    if max_score <= 0:
        return 0.0

    return round(total_score / max_score, 6)


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


def build_workspace_record_filters(search: SearchRequest, workspace_id: str) -> list[object]:
    filters: list[object] = [ProvenanceRecord.workspace_id == workspace_id]

    model_filter = (search.model or "").strip().lower()
    if model_filter:
        filters.append(
            func.lower(func.coalesce(ProvenanceRecord.model_name, "")).like(f"%{model_filter}%")
        )

    file_filter = normalize_search_path(search.file_path or search.current_file or "")
    if file_filter:
        normalized_record_path = func.lower(
            func.replace(ProvenanceRecord.file_path, "\\", "/")
        )

        if "/" in file_filter and not file_filter.startswith("/"):
            filters.append(
                or_(
                    normalized_record_path == file_filter,
                    normalized_record_path.like(f"%/{file_filter}"),
                )
            )
        else:
            filters.append(normalized_record_path == file_filter)

    if search.date_from:
        filters.append(ProvenanceRecord.timestamp_iso >= search.date_from)

    if search.date_to:
        filters.append(ProvenanceRecord.timestamp_iso <= search.date_to)

    return filters


def normalize_search_path(value: str) -> str:
    return (value or "").strip().replace("\\", "/").lower()
