from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import uuid as uuid_pkg
from typing import Any

from app.core.model_names import normalize_model_name as _normalize_model_name_canonical
from app.services.confidence_service import ConfidenceInputs, compute_confidence


PROVENANCE_EVENT_SCHEMA_VERSION = "lineagelens.provenance-event.v1"


@dataclass(slots=True)
class NormalizedIngestPayload:
    record_uuid: uuid_pkg.UUID
    request_uuid: uuid_pkg.UUID | None
    timestamp_iso: datetime
    workspace_id: str
    file_path: str
    file_uri: str | None
    language_id: str | None
    cursor_line: int | None
    cursor_column: int | None
    inserted_text: str
    net_added_lines: int
    prompt_messages: object | None
    model_name: str | None
    model_parameters: dict[str, Any] | None
    raw_model_response: str | None
    raw_model_response_base64: str | None
    surrounding_context: dict[str, Any] | None
    context_snapshot: dict[str, Any] | None
    embeddings: dict[str, Any] | None
    ast_snapshot: dict[str, Any] | None
    normalized_event: dict[str, Any]
    provenance_payload: dict[str, Any]
    raw_payload: dict[str, Any]
    warnings: list[str]
    capture_status: str
    prompt_status: str
    agent_context: dict[str, Any] | None
    # Dynamic routing decision (None when routing was not applied).
    routing_decision: dict[str, Any] | None = None
    # Evidence-weighted confidence breakdown (None when unavailable).
    confidence_breakdown: list[dict[str, Any]] | None = None


def extract_workspace_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None

    for path in (
        ("workspaceId",),
        ("workspace_id",),
        ("workspace",),
        ("source", "workspace"),
        ("source", "workspaceId"),
        ("file", "workspace"),
        ("file", "workspaceId"),
        ("metadata", "workspaceId"),
    ):
        value = _get_path(payload, list(path))
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _patch_normalized_event_defaults(
    normalized_event: dict[str, Any],
    *,
    schema_version: str,
    event_id: str,
    capture_status: str,
    prompt_status: str,
    file_path: str,
    file_uri: str | None,
    language_id: str | None,
    workspace_id: str,
    inserted_text: str,
    net_added_lines: int,
    context_snapshot: dict[str, Any] | None,
) -> None:
    normalized_event.setdefault("schemaVersion", schema_version)
    normalized_event.setdefault("eventId", event_id)
    normalized_event.setdefault("capture", {})
    if isinstance(normalized_event["capture"], dict):
        normalized_event["capture"].setdefault("level", capture_status)
        normalized_event["capture"].setdefault("promptStatus", prompt_status)
    normalized_event.setdefault("file", {})
    if isinstance(normalized_event["file"], dict):
        normalized_event["file"].setdefault("path", file_path)
        normalized_event["file"].setdefault("uri", file_uri)
        normalized_event["file"].setdefault("languageId", language_id or "unknown")
        normalized_event["file"].setdefault("workspace", workspace_id)
    normalized_event.setdefault("diff", {})
    if isinstance(normalized_event["diff"], dict):
        normalized_event["diff"].setdefault("insertedText", inserted_text)
        normalized_event["diff"].setdefault("netAddedLines", net_added_lines)
    normalized_event.setdefault("context", {})
    if isinstance(normalized_event["context"], dict):
        normalized_event["context"].setdefault("snapshot", context_snapshot)
    normalized_event.setdefault("confidence", {})
    if isinstance(normalized_event["confidence"], dict):
        normalized_event["confidence"].setdefault("correlation", 0.0)


def _resolve_capture_status(
    capture: dict[str, Any],
    payload: dict[str, Any],
    metadata: dict[str, Any],
    inserted_text: str,
    prompt_messages: object | None,
    raw_model_response: str | None,
) -> str:
    status = _normalize_capture_status(
        _first_string(capture, ["level"])
        or _first_string(payload, ["captureStatus"])
        or _first_string(metadata, ["captureStatus"])
        or _first_string(_extract_mapping(payload, ["normalizedEvent", "capture"]) or {}, ["level"])
        or _first_string(_extract_mapping(payload, ["correlation"]) or {}, ["captureStatus"])
    )
    if status == "unavailable" and inserted_text.strip() and not _prompt_was_captured(
        prompt_messages, raw_model_response
    ):
        return "file_diff"
    return status


def normalize_ingest_payload(
    payload: object,
    *,
    workspace_id: str,
) -> NormalizedIngestPayload:
    if not isinstance(payload, dict):
        raise ValueError("Ingest payload must be a JSON object.")

    raw_payload = deepcopy(payload)
    record_uuid = _parse_uuid(
        _first_string_from_keys(payload, ["id", "eventId", "uuid"])
    ) or uuid_pkg.uuid4()
    request_uuid = _parse_uuid(_first_string(payload, ["requestUuid"]) or None)

    timestamp_iso = _parse_timestamp(payload) or datetime.now(tz=UTC)
    file_path = _extract_file_path(payload)
    file_uri = _extract_file_uri(payload)
    language_id = _extract_language_id(payload)
    cursor_line, cursor_column = _extract_cursor(payload)
    inserted_text = _extract_inserted_text(payload)
    net_added_lines = _extract_net_added_lines(payload, inserted_text)
    prompt_messages, model_name, model_parameters, raw_model_response, raw_model_response_base64 = (
        _extract_prompt_payload(payload)
    )
    surrounding_context = _extract_surrounding_context(payload)
    context_snapshot = _extract_mapping(payload, ["contextSnapshot"]) or _extract_mapping(
        payload, ["context", "snapshot"]
    )
    embeddings = _extract_mapping(payload, ["embeddings"])
    ast_snapshot = _extract_mapping(payload, ["astSnapshot"])
    git_branch = _extract_git_branch(payload)

    source = _extract_mapping(payload, ["source"]) or {}
    capture = _extract_mapping(payload, ["capture"]) or {}
    diff = _extract_mapping(payload, ["diff"]) or {}
    prompt = _extract_mapping(payload, ["prompt"]) or {}
    extensions = _extract_mapping(payload, ["extensions"]) or {}
    metadata = _extract_mapping(payload, ["metadata"]) or {}
    inserted_chunks = _extract_chunks(payload, inserted_text)

    prompt_status = _resolve_prompt_status(capture, payload, prompt_messages, raw_model_response)
    capture_status = _resolve_capture_status(
        capture, payload, metadata, inserted_text, prompt_messages, raw_model_response
    )

    agent_context = _resolve_agent_context(
        metadata=metadata,
        record_uuid=record_uuid,
        timestamp_iso=timestamp_iso,
        workspace_id=workspace_id,
        source=source,
        model_name=model_name,
        request_uuid=request_uuid,
        prompt_status=prompt_status,
        capture_status=capture_status,
    )

    normalized_event = _resolve_normalized_event(
        payload=payload,
        raw_payload=raw_payload,
        record_uuid=record_uuid,
        timestamp_iso=timestamp_iso,
        workspace_id=workspace_id,
        file_path=file_path,
        file_uri=file_uri,
        language_id=language_id,
        source=source,
        capture=capture,
        model_name=model_name,
        model_parameters=model_parameters,
        prompt_messages=prompt_messages,
        raw_model_response=raw_model_response,
        raw_model_response_base64=raw_model_response_base64,
        inserted_text=inserted_text,
        net_added_lines=net_added_lines,
        surrounding_context=surrounding_context,
        context_snapshot=context_snapshot,
        request_uuid=request_uuid,
        agent_context=agent_context,
        capture_status=capture_status,
        prompt_status=prompt_status,
    )

    # ── Evidence-weighted confidence ──────────────────────────────────────────
    # Build ConfidenceInputs from already-extracted fields, compute the result,
    # then patch both agent_context and normalized_event before the provenance
    # payload is assembled (so provenance_payload.normalizedEvent inherits the
    # updated values automatically).

    # Prompt timestamp: try several probe paths in the raw payload.
    _prompt_ts: datetime | None = None
    for _ts_path in (
        ["timestamps", "requestAtIso"],
        ["requestTimestampIso"],
        ["correlation", "proxyRequestTimestampIso"],
    ):
        _ts_str = _first_string(payload, _ts_path)
        if _ts_str:
            try:
                _prompt_ts = _normalize_datetime(datetime.fromisoformat(_ts_str.replace("Z", "+00:00")))
                break
            except ValueError:
                pass

    # Whether proxy & editor both confirmed the same request UUID.
    _uuid_matched = bool(
        payload.get("requestUuidMatchesCapture")
        or (capture or {}).get("requestUuidMatchesCapture")
    )

    _conf_inputs = ConfidenceInputs(
        capture_status=capture_status,
        request_uuid_present=request_uuid is not None,
        request_uuid_matches_capture=_uuid_matched,
        prompt_timestamp=_prompt_ts,
        insertion_timestamp=timestamp_iso,
        raw_model_response=raw_model_response,
        inserted_text=inserted_text,
        tool_name=_first_string(source, ["toolName"]),
        user_agent=_first_string(source, ["userAgent"]),
        provider=_first_string(source, ["provider"]),
    )
    _conf_result = compute_confidence(_conf_inputs)

    # Compute evidence dicts once and reuse across all aliases (avoids 4x serialisation).
    _evidence_dicts = [e.to_dict() for e in _conf_result.evidence]

    # Patch agent_context in-place (skip when build_agent_context returned None).
    if isinstance(agent_context, dict):
        agent_context["confidence"] = _conf_result.value
        # Two aliases kept for backward compat: callers may use either key.
        agent_context["confidenceEvidence"] = _evidence_dicts
        agent_context["evidence"] = agent_context["confidenceEvidence"]  # D16: same list, two keys

    # Patch normalized_event in-place.
    if isinstance(normalized_event, dict):
        normalized_event["confidence"] = _conf_result.to_dict()
        _corr = normalized_event.get("correlation")
        if isinstance(_corr, dict):
            _corr["confidence"] = _conf_result.value

    # ── End confidence ────────────────────────────────────────────────────────

    provenance_payload = _build_legacy_provenance_payload(_LegacyPayloadParams(
        payload=raw_payload,
        record_uuid=record_uuid,
        request_uuid=request_uuid,
        timestamp_iso=timestamp_iso,
        workspace_id=workspace_id,
        file_path=file_path,
        file_uri=file_uri,
        language_id=language_id,
        cursor_line=cursor_line,
        cursor_column=cursor_column,
        inserted_text=inserted_text,
        net_added_lines=net_added_lines,
        inserted_chunks=inserted_chunks,
        prompt_messages=prompt_messages,
        model_name=model_name,
        model_parameters=model_parameters,
        raw_model_response=raw_model_response,
        raw_model_response_base64=raw_model_response_base64,
        surrounding_context=surrounding_context,
        context_snapshot=context_snapshot,
        embeddings=embeddings,
        ast_snapshot=ast_snapshot,
        normalized_event=normalized_event,
        agent_context=agent_context,
        capture_status=capture_status,
        prompt_status=prompt_status,
        git_branch=git_branch,
        diff=diff,
        prompt=prompt,
        source=source,
        capture=capture,
        extensions=extensions,
    ))

    warnings = _build_ingest_warnings(raw_payload, capture_status)

    # Extract routing decision block sent by the proxy (if present).
    routing_decision: dict[str, Any] | None = None
    _routing_raw = payload.get("routing")
    if isinstance(_routing_raw, dict) and _routing_raw:
        routing_decision = _routing_raw

    return NormalizedIngestPayload(
        record_uuid=record_uuid,
        request_uuid=request_uuid,
        timestamp_iso=timestamp_iso,
        workspace_id=workspace_id,
        file_path=file_path,
        file_uri=file_uri,
        language_id=language_id,
        cursor_line=cursor_line,
        cursor_column=cursor_column,
        inserted_text=inserted_text,
        net_added_lines=net_added_lines,
        prompt_messages=prompt_messages,
        model_name=model_name,
        model_parameters=model_parameters,
        raw_model_response=raw_model_response,
        raw_model_response_base64=raw_model_response_base64,
        surrounding_context=surrounding_context,
        context_snapshot=context_snapshot,
        embeddings=embeddings,
        ast_snapshot=ast_snapshot,
        normalized_event=normalized_event,
        provenance_payload=provenance_payload,
        raw_payload=raw_payload,
        warnings=warnings,
        capture_status=capture_status,
        prompt_status=prompt_status,
        agent_context=agent_context,
        routing_decision=routing_decision,
        confidence_breakdown=_evidence_dicts,
    )


def _resolve_prompt_status(
    capture: dict[str, Any],
    payload: dict[str, Any],
    prompt_messages: object | None,
    raw_model_response: str | None,
) -> str:
    status = _normalize_prompt_status(
        _first_string(capture, ["promptStatus"]) or _first_string(payload, ["promptStatus"])
    )
    if status is None:
        return "captured" if _prompt_was_captured(prompt_messages, raw_model_response) else "not-captured"
    return status


def _resolve_agent_context(
    *,
    metadata: dict[str, Any],
    record_uuid: uuid_pkg.UUID,
    timestamp_iso: datetime,
    workspace_id: str,
    source: dict[str, Any],
    model_name: str | None,
    request_uuid: uuid_pkg.UUID | None,
    prompt_status: str,
    capture_status: str,
) -> dict[str, Any] | None:
    agent_context = _extract_mapping(metadata, ["agentContext"])
    if agent_context is None:
        agent_context = _build_agent_context(
            record_uuid=record_uuid,
            timestamp_iso=timestamp_iso,
            workspace_id=workspace_id,
            source=source,
            model_name=model_name,
            request_uuid=request_uuid,
            prompt_status=prompt_status,
            capture_status=capture_status,
        )
    return agent_context


def _resolve_normalized_event(
    *,
    payload: dict[str, Any],
    raw_payload: dict[str, Any],
    record_uuid: uuid_pkg.UUID,
    timestamp_iso: datetime,
    workspace_id: str,
    file_path: str,
    file_uri: str | None,
    language_id: str | None,
    source: dict[str, Any],
    capture: dict[str, Any],
    model_name: str | None,
    model_parameters: dict[str, Any] | None,
    prompt_messages: object | None,
    raw_model_response: str | None,
    raw_model_response_base64: str | None,
    inserted_text: str,
    net_added_lines: int,
    surrounding_context: dict[str, Any] | None,
    context_snapshot: dict[str, Any] | None,
    request_uuid: uuid_pkg.UUID | None,
    agent_context: dict[str, Any] | None,
    capture_status: str,
    prompt_status: str,
) -> dict[str, Any]:
    existing = _extract_mapping(payload, ["normalizedEvent"])
    if isinstance(existing, dict):
        normalized_event = deepcopy(existing)
    else:
        normalized_event = _build_normalized_event(_EventBuildParams(
            event_uuid=record_uuid,
            timestamp_iso=timestamp_iso,
            workspace_id=workspace_id,
            file_path=file_path,
            file_uri=file_uri,
            language_id=language_id,
            source=source,
            capture=capture,
            model_name=model_name,
            model_parameters=model_parameters,
            prompt_messages=prompt_messages,
            raw_model_response=raw_model_response,
            raw_model_response_base64=raw_model_response_base64,
            inserted_text=inserted_text,
            net_added_lines=net_added_lines,
            surrounding_context=surrounding_context,
            context_snapshot=context_snapshot,
            request_uuid=request_uuid,
            agent_context=agent_context,
            capture_status=capture_status,
            prompt_status=prompt_status,
            raw_payload=raw_payload,
        ))
    if isinstance(normalized_event, dict):
        _patch_normalized_event_defaults(
            normalized_event,
            schema_version=raw_payload.get("schemaVersion") or PROVENANCE_EVENT_SCHEMA_VERSION,
            event_id=str(record_uuid),
            capture_status=capture_status,
            prompt_status=prompt_status,
            file_path=file_path,
            file_uri=file_uri,
            language_id=language_id,
            workspace_id=workspace_id,
            inserted_text=inserted_text,
            net_added_lines=net_added_lines,
            context_snapshot=context_snapshot,
        )
    return normalized_event


def _build_ingest_warnings(raw_payload: dict[str, Any], capture_status: str) -> list[str]:
    warnings: list[str] = []
    if not _first_string_from_keys(raw_payload, ["id", "eventId", "uuid"]):
        warnings.append(
            "Payload missing a client-provided UUID; a random UUID was assigned. "
            "HTTP retries for this event will create duplicate provenance records."
        )
    if capture_status == "file_diff":
        warnings.append("Captured file-diff-only provenance without prompt or response evidence.")
    return warnings


@dataclass(slots=True)
class _EventBuildParams:
    event_uuid: uuid_pkg.UUID
    timestamp_iso: datetime
    workspace_id: str
    file_path: str
    file_uri: str | None
    language_id: str | None
    source: dict[str, Any]
    capture: dict[str, Any]
    model_name: str | None
    model_parameters: dict[str, Any] | None
    prompt_messages: object | None
    raw_model_response: str | None
    raw_model_response_base64: str | None
    inserted_text: str
    net_added_lines: int
    surrounding_context: dict[str, Any] | None
    context_snapshot: dict[str, Any] | None
    request_uuid: uuid_pkg.UUID | None
    agent_context: dict[str, Any] | None
    capture_status: str
    prompt_status: str
    raw_payload: dict[str, Any]


def _build_normalized_event(params: _EventBuildParams) -> dict[str, Any]:
    capture_level = _normalize_capture_level(_first_string(params.capture, ["level"]))

    return {
        "schemaVersion": params.raw_payload.get("schemaVersion") or PROVENANCE_EVENT_SCHEMA_VERSION,
        "eventId": str(params.event_uuid),
        "timestamps": {
            "observedAtIso": params.timestamp_iso.isoformat(),
            "insertedAtIso": params.timestamp_iso.isoformat(),
            "requestAtIso": None,
            "responseAtIso": None,
        },
        "source": {
            "ide": params.source.get("ide") if isinstance(params.source.get("ide"), str) else None,
            "shim": _first_string(params.source, ["shim"]) or "lightweight",
            "toolName": _first_string(params.source, ["toolName"]),
            "provider": _first_string(params.source, ["provider"]),
            "adapterName": _first_string(params.source, ["adapterName"]),
        },
        "capture": {
            "level": capture_level,
            "promptStatus": params.prompt_status,
            "capabilities": _build_capabilities(params.prompt_messages, params.raw_model_response, params.source, params.request_uuid),
            "evidence": params.source.get("evidence") if isinstance(params.source.get("evidence"), list) else [],
        },
        "session": {
            "sessionId": _first_string(params.source, ["sessionId"]),
            "conversationId": _first_string(params.source, ["conversationId"]),
            "runId": _first_string(params.source, ["runId"]),
            "requestId": str(params.request_uuid) if params.request_uuid else None,
            "signature": _session_signature(params.agent_context, params.workspace_id),
        },
        "model": {
            "name": params.model_name,
            "parameters": params.model_parameters,
        },
        "prompt": {
            "body": params.prompt_messages,
            "system": _first_string(params.source, ["systemPrompt"]) or _first_string(params.capture, ["systemPrompt"]),
        },
        "response": {
            "body": params.raw_model_response,
            "bodyBase64": params.raw_model_response_base64,
        },
        "file": {
            "path": params.file_path,
            "uri": params.file_uri,
            "languageId": params.language_id or "unknown",
            "workspace": params.workspace_id,
            "gitBranch": _first_string(params.raw_payload, ["repository", "gitBranch"]),
        },
        "diff": {
            "insertedText": params.inserted_text,
            "chunks": _extract_chunks(params.raw_payload, params.inserted_text),
            "netAddedLines": params.net_added_lines,
        },
        "context": {
            "snapshot": params.context_snapshot,
            "before": _extract_surrounding_context_value(params.surrounding_context, "before"),
            "after": _extract_surrounding_context_value(params.surrounding_context, "after"),
        },
        "correlation": {
            "confidence": _to_float(_first_string(params.capture, ["confidence"])) or 0.0,
            "timingDifferenceMs": None,
            "windowMs": 0,
            "contentSimilarityScore": None,
            "fileContextMatched": False,
            "captureStatus": params.capture_status,
            "requestUuid": str(params.request_uuid) if params.request_uuid else None,
        },
        "confidence": {
            "agent": _to_float(_first_string(params.source, ["confidence"])) if params.source else None,
            "correlation": _to_float(_first_string(params.capture, ["confidence"])) or 0.0,
        },
        "extensions": {
            "operationType": _first_string(params.source, ["operationType"]) or "edit",
            "sessionKind": _first_string(params.source, ["sessionKind"]) or _guess_session_kind(params.source, params.model_name),
            "host": _first_string(params.source, ["host"]),
            "captureStatus": params.capture_status,
            "promptStatus": params.prompt_status,
            "rawPayload": params.raw_payload,
        },
    }


@dataclass(slots=True)
class _LegacyPayloadParams:
    payload: dict[str, Any]
    record_uuid: uuid_pkg.UUID
    request_uuid: uuid_pkg.UUID | None
    timestamp_iso: datetime
    workspace_id: str
    file_path: str
    file_uri: str | None
    language_id: str | None
    cursor_line: int | None
    cursor_column: int | None
    inserted_text: str
    net_added_lines: int
    inserted_chunks: list[dict[str, Any]]
    prompt_messages: object | None
    model_name: str | None
    model_parameters: dict[str, Any] | None
    raw_model_response: str | None
    raw_model_response_base64: str | None
    surrounding_context: dict[str, Any] | None
    context_snapshot: dict[str, Any] | None
    embeddings: dict[str, Any] | None
    ast_snapshot: dict[str, Any] | None
    normalized_event: dict[str, Any]
    agent_context: dict[str, Any] | None
    capture_status: str
    prompt_status: str
    git_branch: str | None
    diff: dict[str, Any]
    prompt: dict[str, Any]
    source: dict[str, Any]
    capture: dict[str, Any]
    extensions: dict[str, Any]


def _build_legacy_provenance_payload(params: _LegacyPayloadParams) -> dict[str, Any]:
    record = deepcopy(params.payload)

    record.setdefault("schemaVersion", PROVENANCE_EVENT_SCHEMA_VERSION)
    record["id"] = str(params.record_uuid)
    record["uuid"] = str(params.record_uuid)
    record.setdefault("eventId", str(params.record_uuid))
    record["timestampIso"] = params.timestamp_iso.isoformat()
    record["insertionTimestampIso"] = params.timestamp_iso.isoformat()
    record["workspaceId"] = params.workspace_id
    record["promptStatus"] = params.prompt_status
    record["prompt"] = _build_prompt_payload(params.prompt_messages, params.model_name, params.model_parameters, params.raw_model_response, params.raw_model_response_base64, params.prompt)
    record["insertion"] = _build_insertion_payload(
        inserted_text=params.inserted_text,
        net_added_lines=params.net_added_lines,
        cursor_line=params.cursor_line,
        cursor_column=params.cursor_column,
        surrounding_context=params.surrounding_context,
        diff=params.diff,
        inserted_chunks=params.inserted_chunks,
    )
    record["file"] = {
        "path": params.file_path,
        "uri": params.file_uri,
        "languageId": params.language_id or "unknown",
    }
    record["repository"] = {"gitBranch": params.git_branch}
    record["contextSnapshot"] = params.context_snapshot
    record["normalizedEvent"] = params.normalized_event
    record["rawData"] = {
        "detectionPayload": deepcopy(params.payload),
        "proxyRequest": _extract_from_record(params.payload, ["rawData", "proxyRequest"]),
        "proxyResponse": _extract_from_record(params.payload, ["rawData", "proxyResponse"]),
        "extensions": params.extensions,
    }
    record["embeddings"] = params.embeddings or {}
    record["astSnapshot"] = params.ast_snapshot or {}
    record["correlation"] = _build_correlation_payload(
        request_uuid=params.request_uuid,
        model_name=params.model_name,
        model_parameters=params.model_parameters,
        prompt_messages=params.prompt_messages,
        raw_model_response=params.raw_model_response,
        raw_model_response_base64=params.raw_model_response_base64,
        source=params.source,
        capture=params.capture,
        capture_status=params.capture_status,
    )
    record["metadata"] = _build_metadata_payload(
        capture_status=params.capture_status,
        prompt_status=params.prompt_status,
        agent_context=params.agent_context,
        raw_payload=params.payload,
    )

    return record


def _build_prompt_payload(
    prompt_messages: object | None,
    model_name: str | None,
    model_parameters: dict[str, Any] | None,
    raw_model_response: str | None,
    raw_model_response_base64: str | None,
    prompt: dict[str, Any],
) -> dict[str, Any]:
    return {
        "fullMessages": prompt_messages if prompt_messages is not None else prompt.get("fullMessages"),
        "modelName": model_name if model_name is not None else _first_string(prompt, ["modelName"]),
        "parameters": model_parameters if model_parameters is not None else _extract_mapping(prompt, ["parameters"]),
        "rawModelResponse": raw_model_response if raw_model_response is not None else _first_string(prompt, ["rawModelResponse"]),
        "rawModelResponseBase64": raw_model_response_base64 if raw_model_response_base64 is not None else _first_string(prompt, ["rawModelResponseBase64"]),
    }


def _build_insertion_payload(
    *,
    inserted_text: str,
    net_added_lines: int,
    cursor_line: int | None,
    cursor_column: int | None,
    surrounding_context: dict[str, Any] | None,
    diff: dict[str, Any],
    inserted_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "extractedInsertedCodeBlock": inserted_text,
        "insertedChunks": inserted_chunks or _extract_chunks_from_diff(diff),
        "netAddedLines": net_added_lines,
        "cursorPosition": {
            "line": cursor_line,
            "column": cursor_column,
        },
        "surroundingContext": surrounding_context
        or {
            "before": _first_string(diff, ["before"]),
            "after": _first_string(diff, ["after"]),
            "tokenWindow": 200,
        },
    }


def _build_correlation_payload(
    *,
    request_uuid: uuid_pkg.UUID | None,
    model_name: str | None,
    model_parameters: dict[str, Any] | None,
    prompt_messages: object | None,
    raw_model_response: str | None,
    raw_model_response_base64: str | None,
    source: dict[str, Any],
    capture: dict[str, Any],
    capture_status: str,
) -> dict[str, Any]:
    return {
        "promptStatus": _normalize_prompt_status(_first_string(capture, ["promptStatus"]))
        or "not-captured",
        "captureStatus": capture_status,
        "requestUuid": str(request_uuid) if request_uuid else None,
        "timingDifferenceMs": None,
        "correlationWindowMs": 0,
        "similarityThreshold": 0.0,
        "correlationConfidence": _to_float(_first_string(capture, ["confidence"])) or 0.0,
        "fileContextMatched": False,
        "matchedFileContextTokens": [],
        "contentSimilarityApplied": False,
        "ambiguityResolvedByContent": False,
        "contentSimilarityScore": None,
        "proxyResponseTimestampIso": None,
        "proxyRequestTimestampIso": None,
        "fullPromptMessages": prompt_messages,
        "modelName": model_name,
        "parameters": model_parameters,
        "targetHost": _first_string(source, ["host"]),
        "requestHeaders": None,
        "systemPrompt": _first_string(source, ["systemPrompt"]),
        "rawModelResponse": raw_model_response,
        "rawModelResponseBase64": raw_model_response_base64,
        "captureEvidence": None,
    }


def _build_metadata_payload(
    *,
    capture_status: str,
    prompt_status: str,
    agent_context: dict[str, Any] | None,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    metadata = _extract_mapping(raw_payload, ["metadata"]) or {}
    metadata = deepcopy(metadata)
    metadata.setdefault("featureVersion", "backend-ingest-normalizer-v1")
    metadata.setdefault("captureStatus", capture_status)
    metadata.setdefault("promptStatus", prompt_status)
    if agent_context is not None:
        metadata.setdefault("agentContext", agent_context)
    return metadata


def _build_agent_context(
    *,
    record_uuid: uuid_pkg.UUID,
    timestamp_iso: datetime,
    workspace_id: str,
    source: dict[str, Any],
    model_name: str | None,
    request_uuid: uuid_pkg.UUID | None,
    prompt_status: str,
    capture_status: str,
) -> dict[str, Any] | None:
    tool_name = _first_string(source, ["toolName"])
    provider = _first_string(source, ["provider"])
    user_agent = _first_string(source, ["userAgent"])
    session_id = _first_string(source, ["sessionId"]) or str(request_uuid or record_uuid)

    if not tool_name and not provider and not model_name and prompt_status != "captured":
        return None

    session_kind = _guess_session_kind(source, model_name)

    return {
        "toolName": tool_name,
        "provider": provider,
        "sessionId": session_id,
        "conversationId": _first_string(source, ["conversationId"]),
        "runId": _first_string(source, ["runId"]),
        "workspaceHint": workspace_id,
        "operationType": _first_string(source, ["operationType"]) or "edit",
        # confidence / confidenceEvidence are filled by compute_confidence() in
        # normalize_ingest_payload after _build_agent_context returns.
        "confidence": None,
        "confidenceEvidence": [],
        "evidence": [],
        "adapterName": _first_string(source, ["shim"]) or "lightweight",
        "matchSource": "heuristic",
        "sessionKind": session_kind,
        "host": _first_string(source, ["host"]),
        "userAgent": user_agent,
        "modelName": model_name,
        "sessionSignature": _session_signature(
            {
                "toolName": tool_name,
                "provider": provider,
                "modelName": model_name,
                "sessionKind": session_kind,
                "sessionId": session_id,
                "conversationId": _first_string(source, ["conversationId"]),
                "runId": _first_string(source, ["runId"]),
            },
            workspace_id,
        ),
        "detectedAtIso": timestamp_iso.isoformat(),
    }


def _build_capabilities(
    prompt_messages: object | None,
    raw_model_response: str | None,
    source: dict[str, Any],
    request_uuid: uuid_pkg.UUID | None,
) -> list[dict[str, str]]:
    return [
        {"name": "prompt-body", "status": "provided" if prompt_messages is not None else "missing"},
        {"name": "response-body", "status": "provided" if raw_model_response else "missing"},
        {"name": "request-id", "status": "provided" if request_uuid else "missing"},
        {"name": "tool-name", "status": "provided" if _first_string(source, ["toolName"]) else "missing"},
        {"name": "provider", "status": "provided" if _first_string(source, ["provider"]) else "missing"},
        {"name": "file-diff", "status": "provided"},
        {"name": "workspace", "status": "provided" if _first_string(source, ["workspaceHint"]) else "missing"},
    ]


def _extract_file_path(payload: dict[str, Any]) -> str:
    for path in (
        ["filePath"],
        ["file", "path"],
        ["path"],
        ["source", "filePath"],
        ["diff", "filePath"],
    ):
        value = _first_string(payload, path)
        if value:
            return value

    raise ValueError("Ingest payload is missing a file path.")


def _extract_file_uri(payload: dict[str, Any]) -> str | None:
    return _first_string(payload, ["fileUri"]) or _first_string(payload, ["file", "uri"])


def _extract_language_id(payload: dict[str, Any]) -> str | None:
    return _first_string(payload, ["file", "languageId"]) or _first_string(payload, ["languageId"])


def _extract_cursor(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    cursor = _extract_mapping(payload, ["cursor"]) or _extract_mapping(payload, ["insertion", "cursorPosition"])
    if not cursor:
        return None, None

    return _to_int(cursor.get("line")), _to_int(cursor.get("column"))


def _extract_inserted_text(payload: dict[str, Any]) -> str:
    for path in (
        ["insertedText"],
        ["insertion", "extractedInsertedCodeBlock"],
        ["diff", "insertedText"],
        ["diff", "text"],
        ["diff", "body"],
    ):
        value = _first_string(payload, path)
        if value is not None:
            return value

    return ""


def _count_from_inserted_chunks(payload: dict[str, Any]) -> int | None:
    inserted_chunks = _get_path(payload, ["insertion", "insertedChunks"])
    if not isinstance(inserted_chunks, list):
        return None
    total = sum(
        _to_int(c.get("addedLines")) or 0
        for c in inserted_chunks
        if isinstance(c, dict)
    )
    return total if total > 0 else None


def _extract_net_added_lines(payload: dict[str, Any], inserted_text: str) -> int:
    for path in (
        ["netAddedLines"],
        ["insertion", "netAddedLines"],
        ["diff", "netAddedLines"],
    ):
        value = _to_int(_get_path(payload, path))
        if value is not None:
            return value

    chunk_count = _count_from_inserted_chunks(payload)
    if chunk_count is not None:
        return chunk_count

    if inserted_text.strip():
        return max(1, inserted_text.count("\n") + 1)

    return 0


def _resolve_prompt_messages(payload: dict[str, Any]) -> object | None:
    return (
        _get_path(payload, ["prompt", "fullMessages"])
        or _get_path(payload, ["prompt", "body"])
        or _get_path(payload, ["provenance", "fullPromptMessages"])
        or _get_path(payload, ["normalizedEvent", "prompt", "body"])
        or _get_path(payload, ["correlation", "fullPromptMessages"])
        or _get_path(payload, ["messages"])
        or _get_path(payload, ["promptMessages"])
        or _get_path(payload, ["prompt_messages"])
    )


def _resolve_model_name(
    payload: dict[str, Any],
    prompt: dict[str, Any] | None,
    provenance: dict[str, Any] | None,
    model: dict[str, Any],
    source: dict[str, Any],
    correlation: dict[str, Any],
) -> str | None:
    _model_str = payload.get("model") if isinstance(payload.get("model"), str) else None
    raw = (
        _first_string(prompt or {}, ["modelName"])
        or _first_string(provenance or {}, ["modelName"])
        or _first_string(model, ["name"])
        or _first_string(source, ["modelName"])
        or _first_string(correlation, ["modelName"])
        or _model_str
        or _first_string(payload, ["modelName"])
        or _first_string(payload, ["model_name"])
    )
    return _normalize_model_name_canonical(raw)


def _extract_prompt_payload(
    payload: dict[str, Any],
) -> tuple[object | None, str | None, dict[str, Any] | None, str | None, str | None]:
    prompt = _extract_mapping(payload, ["prompt"])
    provenance = _extract_mapping(payload, ["provenance"])
    correlation = _extract_mapping(payload, ["correlation"]) or {}
    source = _extract_mapping(payload, ["source"]) or {}
    model = _extract_mapping(payload, ["model"]) or {}
    response = _extract_mapping(payload, ["response"]) or {}

    prompt_messages = _resolve_prompt_messages(payload)
    model_name = _resolve_model_name(payload, prompt, provenance, model, source, correlation)

    model_parameters = (
        _extract_mapping(prompt or {}, ["parameters"])
        or _extract_mapping(provenance or {}, ["parameters"])
        or _extract_mapping(model, ["parameters"])
        or _extract_mapping(correlation, ["parameters"])
    )

    raw_model_response = (
        _first_string(prompt or {}, ["rawModelResponse"])
        or _first_string(provenance or {}, ["rawModelResponse"])
        or _first_string(response, ["body"])
        or _first_string(correlation, ["rawModelResponse"])
        or _first_string(payload, ["rawModelResponse"])
        or _first_string(payload, ["raw_model_response"])
    )

    raw_model_response_base64 = (
        _first_string(prompt or {}, ["rawModelResponseBase64"])
        or _first_string(provenance or {}, ["rawModelResponseBase64"])
        or _first_string(response, ["bodyBase64"])
        or _first_string(correlation, ["rawModelResponseBase64"])
    )

    return prompt_messages, model_name, model_parameters, raw_model_response, raw_model_response_base64


def _extract_surrounding_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    surrounding_context = _extract_mapping(payload, ["surroundingContext"])
    if surrounding_context is not None:
        return surrounding_context

    context = _extract_mapping(payload, ["context"])
    if context is None:
        return None

    before = context.get("before")
    after = context.get("after")
    token_window = context.get("tokenWindow") or context.get("token_window")

    if before is None and after is None and token_window is None:
        return None

    return {
        "before": before,
        "after": after,
        "tokenWindow": token_window or 200,
    }


def _extract_git_branch(payload: dict[str, Any]) -> str | None:
    return _first_string(payload, ["repository", "gitBranch"]) or _first_string(payload, ["activeGitBranch"])


def _extract_chunks(payload: dict[str, Any], inserted_text: str) -> list[dict[str, Any]]:
    chunks = _extract_chunks_from_diff(_extract_mapping(payload, ["diff"]) or {})
    if chunks:
        return chunks

    insertion_chunks = _get_path(payload, ["insertion", "insertedChunks"])
    if isinstance(insertion_chunks, list):
        return [chunk for chunk in insertion_chunks if isinstance(chunk, dict)]

    if not inserted_text:
        return []

    lines = inserted_text.split("\n")
    last_line_len = len(lines[-1])
    end_line = len(lines)
    return [
        {
            "text": inserted_text,
            "start": {"line": 1, "column": 1},
            "end": {"line": end_line, "column": 1 + last_line_len},
            "addedLines": max(1, end_line),
            "removedLines": 0,
        }
    ]


def _extract_chunks_from_diff(diff: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = diff.get("chunks")
    if isinstance(chunks, list):
        return [chunk for chunk in chunks if isinstance(chunk, dict)]

    return []


def _extract_surrounding_context_value(
    surrounding_context: dict[str, Any] | None,
    key: str,
) -> Any:
    if not isinstance(surrounding_context, dict):
        return None

    return surrounding_context.get(key)


def _session_signature(agent_context: dict[str, Any] | None, workspace_id: str) -> str:
    parts = [
        _first_string(agent_context or {}, ["toolName"]) or "unknown-tool",
        _first_string(agent_context or {}, ["provider"]) or "unknown-provider",
        _first_string(agent_context or {}, ["modelName"]) or "unknown-model",
        _first_string(agent_context or {}, ["sessionKind"]) or "unknown",
        _first_string(agent_context or {}, ["sessionId"]) or workspace_id,
    ]

    return hashlib.sha1("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _guess_session_kind(source: dict[str, Any], model_name: str | None) -> str:
    tool_name = _first_string(source, ["toolName"]) or ""
    provider = _first_string(source, ["provider"]) or ""
    shim = _first_string(source, ["shim"]) or ""
    text = " ".join([tool_name, provider, shim, model_name or ""]).lower()

    if any(token in text for token in ("cursor", "claude", "aider", "codex")):
        return "agentic"

    if tool_name or provider:
        return "assistant"

    return "unknown"


def _normalize_prompt_status(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip().lower()
    if normalized in {"captured", "not-captured"}:
        return normalized

    return None


def _normalize_capture_status(value: str | None) -> str:
    if not value:
        return "file_diff"

    normalized = value.strip().lower()
    if normalized in {"full", "metadata_only", "tunnel_only", "unavailable", "file_diff"}:
        return normalized

    return "file_diff"


def _normalize_capture_level(value: str | None) -> str:
    if not value:
        return "file_diff"

    normalized = value.strip().lower()
    return normalized or "file_diff"


def _prompt_was_captured(prompt_messages: object | None, raw_model_response: str | None) -> bool:
    if prompt_messages is not None:
        return True

    if raw_model_response and raw_model_response.strip():
        return True

    return False


def _parse_uuid(value: str | None) -> uuid_pkg.UUID | None:
    if not value:
        return None

    try:
        return uuid_pkg.UUID(value)
    except (ValueError, TypeError):
        return None


def _parse_timestamp(payload: dict[str, Any]) -> datetime | None:
    for path in (
        ["timestampIso"],
        ["timestamp_iso"],
        ["timestamps", "insertedAtIso"],
        ["timestamps", "observedAtIso"],
        ["createdAtIso"],
    ):
        value = _first_string(payload, path)
        if not value:
            continue

        try:
            return _normalize_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            continue

    return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _extract_mapping(payload: dict[str, Any], path: list[str]) -> dict[str, Any] | None:
    value = _get_path(payload, path)
    if isinstance(value, dict):
        return value

    return None


def _get_path(payload: dict[str, Any], path: list[str]) -> Any:
    cursor: Any = payload
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return None
        cursor = cursor[segment]
    return cursor


def _extract_from_record(payload: dict[str, Any], path: list[str]) -> Any:
    return _get_path(payload, path)


def _first_string(payload: dict[str, Any], path: list[str]) -> str | None:
    value = _get_path(payload, path)
    if isinstance(value, str):
        text = value.strip()
        return text if text else None

    if value is None:
        return None

    text = str(value).strip()
    return text if text else None


def _first_string_from_keys(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
            continue

        if value is None:
            continue

        text = str(value).strip()
        if text:
            return text

    return None


def _to_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None

    return None


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None

    return None