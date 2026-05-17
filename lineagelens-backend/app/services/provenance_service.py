from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import math
import re
import uuid as uuid_pkg
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import AuthContext
from app.db.models import ProvenanceRecord, ProvenanceTag
from app.schemas.provenance import SearchRequest, decode_cursor, encode_cursor
from app.services.ast_normalizer import normalize_ast_tokens
from app.services.embedding_service import generate_embedding
from app.services.ingest_normalizer import NormalizedIngestPayload
from app.services.neo4j_service import Neo4jLineageService
from app.services.risk_service import compute_risk_score


logger = logging.getLogger(__name__)

_BG_TASKS: set[asyncio.Task] = set()


@dataclass(slots=True)
class IngestOutcome:
    record: ProvenanceRecord
    warnings: list[str]
    stored: bool = True


def infer_language_hint(file_path: str) -> str:
    lowered = (file_path or "").lower()
    if lowered.endswith((".py", ".pyi")):
        return "python"
    if lowered.endswith((".tsx", ".jsx")):
        return "tsx"
    if lowered.endswith((".ts", ".mts", ".cts")):
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
    app_state: object | None = None,
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
        return IngestOutcome(record=existing_record, warnings=warnings, stored=False)

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

    risk_score_value, _risk_reasons = compute_risk_score(
        inserted_code=payload.inserted_text,
        prompt_messages=payload.prompt_messages,
        model_name=payload.model_name,
        file_path=payload.file_path,
    )

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
        risk_score=risk_score_value,
        provenance_payload=payload.provenance_payload,
    )

    try:
        session.add(record)
        await session.flush()

        lineage_version_id = await _write_lineage_node(
            record=record,
            ast_tokens=ast_tokens,
            timestamp=timestamp,
            neo4j_service=neo4j_service,
            session=session,
            settings=settings,
            warnings=warnings,
        )

        await _commit_with_lineage_cleanup(
            session=session,
            lineage_version_id=lineage_version_id,
            neo4j_service=neo4j_service,
        )

        await session.refresh(record)

        if app_state is not None and record.risk_score is not None:
            try:
                from app.api.routes.webhooks import trigger_webhooks  # local import to avoid circular
                _wh_task = asyncio.create_task(
                    trigger_webhooks(
                        app_state,
                        workspace_id=record.workspace_id,
                        record_uuid=str(record.uuid),
                        risk_score=record.risk_score,
                        file_path=record.file_path,
                    )
                )
                _BG_TASKS.add(_wh_task)
                _wh_task.add_done_callback(_BG_TASKS.discard)
            except Exception as webhook_error:
                logger.debug("Webhook trigger skipped: %s", webhook_error)

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
            return IngestOutcome(record=existing_record, warnings=warnings, stored=False)

        raise

    return IngestOutcome(record=record, warnings=warnings)


async def _write_lineage_node(
    *,
    record: ProvenanceRecord,
    ast_tokens: list[str],
    timestamp: Any,
    neo4j_service: Neo4jLineageService | None,
    session: AsyncSession,
    settings: Settings,
    warnings: list[str],
) -> str | None:
    if neo4j_service is None:
        warnings.append("Neo4j lineage is disabled; record stored without graph lineage.")
        return None

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
        return lineage_version_id
    except Exception as error:
        if settings.lineage_strict_mode:
            await session.rollback()
            raise
        warnings.append("Neo4j lineage is unavailable; record stored without graph lineage.")
        warnings.append(f"Neo4j lineage creation failed: {error}")
        return None


async def _commit_with_lineage_cleanup(
    *,
    session: AsyncSession,
    lineage_version_id: str | None,
    neo4j_service: Neo4jLineageService | None,
) -> None:
    try:
        await session.commit()
    except Exception:
        # If the Postgres commit fails after a Neo4j node was written, the graph node has no
        # matching provenance record. Clean it up to avoid a zombie node.
        if lineage_version_id is not None and neo4j_service is not None:
            try:
                await neo4j_service.delete_lineage_record(record_uuid=lineage_version_id)
            except Exception as cleanup_error:
                logger.warning(
                    "Neo4j orphan cleanup failed after Postgres commit error: %s", cleanup_error
                )
        raise


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


async def _vector_search(
    session: AsyncSession,
    query_text: str,
    filters: list[object],
    offset: int,
    limit: int,
    settings: Settings,
) -> tuple[list[tuple[ProvenanceRecord, float | None]], list[str], int | None, str | None]:
    query_embedding = await generate_embedding(query_text, settings.pgvector_dimension, settings)

    # Guard: skip vector path if embedding is None or all-zeros
    if query_embedding is None or all(math.isclose(v, 0.0) for v in query_embedding):
        logger.warning(
            "Vector search skipped: query embedding is null or zero-vector; falling back to keyword search"
        )
        return await _keyword_search(session, query_text, filters, offset, limit)

    distance_expr = ProvenanceRecord.embedding_vector.cosine_distance(query_embedding).label(
        "distance"
    )

    statement = (
        select(ProvenanceRecord, distance_expr)
        .where(and_(*filters))
        .where(ProvenanceRecord.embedding_vector.is_not(None))
        .order_by(distance_expr.asc())
        .offset(offset)
        .limit(limit)
    )

    result = await session.execute(statement)
    rows = result.all()
    return [(row[0], to_similarity(row[1])) for row in rows], [], None, None


async def _keyword_search(
    session: AsyncSession,
    query_text: str,
    filters: list[object],
    offset: int,
    limit: int,
) -> tuple[list[tuple[ProvenanceRecord, float | None]], list[str], int | None, str | None]:
    keyword_scan_limit = min((offset + limit) * 10, 2000)
    statement = (
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
        .limit(keyword_scan_limit)
    )

    result = await session.execute(statement)
    records = result.scalars().all()

    tokens = tokenize_query(query_text)
    rows_with_scores: list[tuple[ProvenanceRecord, float | None]] = [
        (record, score)
        for record in records
        for score in (score_keyword_match(record, tokens),)
        if score > 0
    ]

    rows_with_scores.sort(
        key=lambda item: (
            item[1] if isinstance(item[1], (int, float)) else 0.0,
            item[0].timestamp_iso,
        ),
        reverse=True,
    )

    warnings = ["Vector search is disabled; using keyword fallback search."]
    return rows_with_scores[offset : offset + limit], warnings, len(rows_with_scores), None


def _apply_cursor_filter(
    cursor: str | None,
    filters: list[object],
    offset: int,
) -> tuple[list[object], int]:
    """Return (cursor_filters, adjusted_offset).

    Decodes the pagination cursor and appends the appropriate SQL filter so the
    query returns only records that come after the cursor position.  If the
    cursor is absent or invalid the original filters and offset are returned
    unchanged.
    """
    cursor_filters = list(filters)
    if not cursor:
        return cursor_filters, offset

    decoded = decode_cursor(cursor)
    if decoded is None:
        return cursor_filters, offset

    cursor_ts_str, cursor_uuid_str = decoded
    try:
        cursor_ts = datetime.fromisoformat(cursor_ts_str.replace("Z", "+00:00"))
        cursor_uuid = uuid_pkg.UUID(cursor_uuid_str)
        # Records strictly older than the cursor timestamp, or same timestamp
        # with a UUID that sorts after the cursor UUID (stable tie-breaking).
        cursor_filters.append(
            or_(
                ProvenanceRecord.timestamp_iso < cursor_ts,
                and_(
                    ProvenanceRecord.timestamp_iso == cursor_ts,
                    ProvenanceRecord.uuid > cursor_uuid,
                ),
            )
        )
        offset = 0  # cursor supersedes offset
    except (ValueError, AttributeError):
        pass  # invalid cursor — fall back to offset

    return cursor_filters, offset


async def _listing_search(
    session: AsyncSession,
    search: SearchRequest,
    filters: list[object],
    offset: int,
    limit: int,
) -> tuple[list[tuple[ProvenanceRecord, float | None]], list[str], int | None, str | None]:
    cursor_filters, offset = _apply_cursor_filter(search.cursor, filters, offset)

    count_statement = select(func.count()).select_from(ProvenanceRecord).where(and_(*filters))
    count_result = await session.execute(count_statement)
    total = count_result.scalar_one()

    statement = (
        select(ProvenanceRecord)
        .where(and_(*cursor_filters))
        .order_by(desc(ProvenanceRecord.timestamp_iso), ProvenanceRecord.uuid)
        .offset(offset)
        .limit(limit)
    )

    result = await session.execute(statement)
    records = result.scalars().all()

    next_cursor: str | None = None
    if len(records) == limit:
        last = records[-1]
        next_cursor = encode_cursor(last.timestamp_iso.isoformat(), str(last.uuid))

    return [(record, None) for record in records], [], total, next_cursor


async def search_provenance_records(
    session: AsyncSession,
    search: SearchRequest,
    workspace_id: str,
    settings: Settings,
) -> tuple[list[tuple[ProvenanceRecord, float | None]], list[str], int | None, str | None]:
    query_text = (search.query or search.keywords or "").strip()

    limit = search.top_k or search.limit or settings.search_default_limit
    limit = min(200, max(1, int(limit)))
    offset = max(0, int(search.offset or 0))

    filters = build_workspace_record_filters(search, workspace_id)

    if query_text and settings.vector_search_enabled:
        return await _vector_search(session, query_text, filters, offset, limit, settings)

    if query_text:
        return await _keyword_search(session, query_text, filters, offset, limit)

    return await _listing_search(session, search, filters, offset, limit)


def serialize_provenance_record(
    record: ProvenanceRecord,
    score: float | None = None,
) -> dict[str, Any]:
    payload = dict(record.provenance_payload or {})

    payload.setdefault("id", str(record.uuid))
    payload.setdefault("uuid", str(record.uuid))
    payload.setdefault("workspaceId", record.workspace_id)
    payload.setdefault("timestampIso", record.timestamp_iso.isoformat())
    payload.setdefault("filePath", record.file_path)
    payload.setdefault("fileUri", record.file_uri)
    payload.setdefault("insertedText", record.inserted_code)
    payload.setdefault("insertedCode", record.inserted_code)
    payload.setdefault("modelName", record.model_name)
    payload.setdefault("riskScore", record.risk_score)
    payload.setdefault("tokenCount", record.token_count)
    payload.setdefault("costUsd", record.cost_usd)
    payload.setdefault("isRedacted", record.is_redacted)
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

    fields = {
        "file_path": (record.file_path or "").lower(),
        "inserted_code": (record.inserted_code or "").lower(),
        "prompt_blob": json.dumps(record.prompt_messages or {}, sort_keys=True, default=str).lower(),
        "model_name": (record.model_name or "").lower(),
        "payload_blob": json.dumps(record.provenance_payload or {}, sort_keys=True, default=str).lower(),
    }

    total_score = sum(_score_token(token, fields) for token in tokens)
    max_score = len(tokens) * 10.0

    if max_score <= 0:
        return 0.0

    return round(total_score / max_score, 6)


def _score_token(token: str, fields: dict[str, str]) -> float:
    score = 0.0
    if token in fields["file_path"]:
        score += 3.0
    if token in fields["inserted_code"]:
        score += 2.0
    if token in fields["prompt_blob"]:
        score += 2.0
    if token in fields["model_name"]:
        score += 2.0
    if token in fields["payload_blob"]:
        score += 1.0
    return score


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


_RISK_LEVEL_RANGES: dict[str, tuple[int, int]] = {
    "critical": (85, 100),
    "high": (65, 84),
    "medium": (35, 64),
    "low": (0, 34),
}


def build_workspace_record_filters(search: SearchRequest, workspace_id: str) -> list[object]:
    filters: list[object] = [ProvenanceRecord.workspace_id == workspace_id]

    # model name filter (supports both 'model' and 'model_name' fields)
    model_filter = (getattr(search, "model_name", None) or search.model or "").strip().lower()
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

    # risk_level mapped to score ranges
    risk_level = getattr(search, "risk_level", None)
    if risk_level:
        level_lower = risk_level.strip().lower()
        if level_lower in _RISK_LEVEL_RANGES:
            lo, hi = _RISK_LEVEL_RANGES[level_lower]
            filters.append(ProvenanceRecord.risk_score >= lo)
            filters.append(ProvenanceRecord.risk_score <= hi)

    risk_min = getattr(search, "risk_min", None)
    if risk_min is not None:
        filters.append(ProvenanceRecord.risk_score >= risk_min)

    risk_max = getattr(search, "risk_max", None)
    if risk_max is not None:
        filters.append(ProvenanceRecord.risk_score <= risk_max)

    # file extension filter
    file_extension = getattr(search, "file_extension", None)
    if file_extension:
        ext = file_extension.strip().lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        filters.append(
            func.lower(ProvenanceRecord.file_path).like(f"%{ext}")
        )

    # capture_status — stored in context_snapshot->captureStatus or provenance_payload
    capture_status = getattr(search, "capture_status", None)
    if capture_status:
        filters.append(
            ProvenanceRecord.provenance_payload["captureStatus"].astext == capture_status
        )

    # agent_tool filter — from contextSnapshot->agentTool
    agent_tool = getattr(search, "agent_tool", None)
    if agent_tool:
        filters.append(
            ProvenanceRecord.context_snapshot["agentTool"].astext == agent_tool
        )

    # has_prompt
    has_prompt = getattr(search, "has_prompt", None)
    if has_prompt is True:
        filters.append(ProvenanceRecord.prompt_messages.is_not(None))
    elif has_prompt is False:
        filters.append(ProvenanceRecord.prompt_messages.is_(None))

    # is_redacted filter
    is_redacted = getattr(search, "is_redacted", None)
    if is_redacted is True:
        filters.append(ProvenanceRecord.is_redacted.is_(True))
    elif is_redacted is False:
        filters.append(ProvenanceRecord.is_redacted.is_(False))

    # tags filter — records that have ALL specified tags
    tags = getattr(search, "tags", None)
    if tags:
        from sqlalchemy import Text, cast

        clean_tags = [t.strip().lower() for t in tags if t.strip()]
        for tag_val in clean_tags:
            tag_subq = (
                select(ProvenanceTag.record_uuid)
                .where(
                    and_(
                        ProvenanceTag.workspace_id == workspace_id,
                        ProvenanceTag.tag == tag_val,
                    )
                )
                .scalar_subquery()
            )
            # ProvenanceTag.record_uuid is stored as String(64) (the UUID stringified),
            # so cast ProvenanceRecord.uuid to text for the IN comparison.
            filters.append(cast(ProvenanceRecord.uuid, Text).in_(tag_subq))

    return filters


def normalize_search_path(value: str) -> str:
    return (value or "").strip().replace("\\", "/").lower()
