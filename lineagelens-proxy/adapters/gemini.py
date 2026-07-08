"""Google Gemini CLI (functionCall / functionResponse) adapter for the LineageLens proxy."""
import asyncio
import hashlib
import json
import logging
import re
import urllib.parse
import uuid

from adapters.common import (
    _annotate_edits,
    _background_tasks,
    _get_ingest_fn,
    _pending_edits,
    _pending_edits_lock,
    _PENDING_EDITS_MAX,
    _SSE_DATA_PREFIX,
    _SSE_DONE_MARKER,
)
from adapters.contract import AdapterCapability

logger = logging.getLogger("lineagelens-proxy")

# PART 5 #54 — each Gemini functionCall maps to exactly one edit (no
# multi-file batch tool exists in _GEMINI_FILE_MUTATING_TOOLS), so
# supports_multi_edit=False, unlike anthropic/codex. SSE assembly and
# functionResponse resolution both exist. Structured args (not text-
# heuristic), hence "full" fidelity.
CAPABILITY = AdapterCapability(
    provider="gemini",
    supports_multi_edit=False,
    supports_streaming=True,
    supports_tool_results=True,
    fidelity="full",
)

# Gemini file-mutating tool names. Both snake_case (current Gemini CLI) and
# PascalCase (older variants) are accepted.
_GEMINI_FILE_MUTATING_TOOLS = {
    "write_file", "WriteFile",
    "create_file", "CreateFile",
    "replace", "Replace",
    "edit", "Edit",
    "edit_file", "EditFile",
}


def _gemini_session_key(body_dict: dict, headers: dict) -> str:
    """Stable session fingerprint for Gemini API."""
    si = body_dict.get("systemInstruction") or body_dict.get("system_instruction") or {}
    instructions = ""
    if isinstance(si, dict):
        parts = si.get("parts", [])
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict):
                    instructions += p.get("text", "") or ""
        elif isinstance(si.get("text"), str):
            instructions = si["text"]
    elif isinstance(si, str):
        instructions = si

    auth = ""
    for header_name in (
        "x-goog-api-key", "X-Goog-Api-Key",
        "authorization", "Authorization",
        "x-api-key", "X-Api-Key",
    ):
        v = headers.get(header_name)
        if v:
            auth = v[:24]
            break

    raw = f"gemini|{instructions[:4096]}|{auth}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _parse_gemini_function_call_to_edits(function_call: dict) -> list[dict]:
    """Convert one Gemini functionCall into one or more edit records."""
    name = function_call.get("name", "")
    if name not in _GEMINI_FILE_MUTATING_TOOLS:
        return []

    args = function_call.get("args") or {}
    if not isinstance(args, dict):
        return []

    file_path = (
        args.get("file_path", "")
        or args.get("filePath", "")
        or args.get("path", "")
    )
    if not file_path or not isinstance(file_path, str):
        return []

    explicit_id = function_call.get("id", "")
    if explicit_id and isinstance(explicit_id, str):
        synthetic_id = explicit_id
    else:
        synthetic_id = f"gemini_{uuid.uuid4().hex[:16]}"

    name_lower = name.lower()

    if name_lower in ("write_file", "writefile", "create_file", "createfile"):
        content = (
            args.get("content", "")
            or args.get("file_content", "")
            or args.get("text", "")
            or ""
        )
        return [{
            "tool_use_id": synthetic_id,
            "tool_name": name,
            "edit_index": 0,
            "file_path": file_path,
            "old_string": "",
            "new_string": content if isinstance(content, str) else str(content),
            "verb": "create" if "create" in name_lower else "write",
            "moved_to": "",
        }]

    if name_lower in ("replace", "edit", "edit_file", "editfile"):
        old_s = (
            args.get("old_string", "")
            or args.get("oldString", "")
            or args.get("old", "")
            or ""
        )
        new_s = (
            args.get("new_string", "")
            or args.get("newString", "")
            or args.get("new", "")
            or ""
        )
        return [{
            "tool_use_id": synthetic_id,
            "tool_name": name,
            "edit_index": 0,
            "file_path": file_path,
            "old_string": old_s if isinstance(old_s, str) else str(old_s),
            "new_string": new_s if isinstance(new_s, str) else str(new_s),
            "verb": "replace",
            "moved_to": "",
        }]

    return []


def _extract_gemini_function_calls_from_body(body: bytes) -> list[dict]:
    """Extract all functionCall parts from a non-streaming Gemini response."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return []

    fcs: list[dict] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("functionCall"), dict):
                fcs.append(part["functionCall"])
    return fcs


def _extract_gemini_function_calls_from_sse(chunks: list[bytes]) -> list[dict]:
    """Extract functionCall parts from a Gemini SSE stream.

    Each SSE data: line is a complete partial GenerateContentResponse.
    Unlike Anthropic, Gemini does NOT split functionCall arguments across
    delta events — each functionCall arrives complete in one chunk.
    """
    fcs: list[dict] = []
    raw = "".join(c.decode("utf-8", errors="replace") for c in chunks)
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        if not line.startswith(_SSE_DATA_PREFIX):
            continue
        payload = line[5:].strip()
        if payload in ("", _SSE_DONE_MARKER):
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue

        candidates = chunk.get("candidates")
        if not isinstance(candidates, list):
            continue
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            content = cand.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("functionCall"), dict):
                    fcs.append(part["functionCall"])
    return fcs


def _extract_gemini_function_responses(body: bytes) -> list[dict]:
    """Extract functionResponses from the LAST response-bearing message."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    contents = data.get("contents")
    if not isinstance(contents, list):
        return []

    for msg in reversed(contents):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role not in ("user", "function"):
            continue
        parts = msg.get("parts")
        if not isinstance(parts, list):
            continue
        msg_responses: list[dict] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("functionResponse"), dict):
                msg_responses.append(part["functionResponse"])
        if msg_responses:
            return msg_responses
    return []


def _extract_gemini_prompt_context(body_dict: dict, url: str) -> dict:
    """Pull model + systemInstruction + contents out of a Gemini request."""
    model = ""
    try:
        path = urllib.parse.urlparse(url).path or ""
        m = re.search(r"/models/([^:/]+)", path)
        if m:
            model = m.group(1)
    except Exception:
        pass

    if not isinstance(body_dict, dict):
        return {"model": model, "system": "", "messages": []}

    si = body_dict.get("systemInstruction") or body_dict.get("system_instruction") or ""
    system = ""
    if isinstance(si, dict):
        parts = si.get("parts", [])
        if isinstance(parts, list):
            chunks = []
            for p in parts:
                if isinstance(p, dict):
                    chunks.append(p.get("text", "") or "")
            system = "\n".join(c for c in chunks if c)
        elif isinstance(si.get("text"), str):
            system = si["text"]
    elif isinstance(si, str):
        system = si

    contents = body_dict.get("contents")
    safe_messages: list[dict] = []
    if isinstance(contents, list):
        for msg in contents:
            if isinstance(msg, dict):
                safe_messages.append(msg)

    return {
        "model": model,
        "system": system,
        "messages": safe_messages,
    }


def _classify_gemini_function_response(fr: dict) -> tuple[str, str]:
    """Map a Gemini functionResponse to (status, error_message)."""
    response = fr.get("response", {})

    if isinstance(response, dict):
        if "error" in response:
            err = response["error"]
            if isinstance(err, dict):
                msg = err.get("message", "") or json.dumps(err)
            else:
                msg = str(err)
            return ("errored", str(msg)[:500])

        output_text = ""
        for k in ("output", "result", "content", "text", "message"):
            if k in response:
                v = response[k]
                output_text = v if isinstance(v, str) else json.dumps(v)
                break
        if not output_text:
            try:
                output_text = json.dumps(response)
            except Exception:
                output_text = str(response)

        lower = output_text.lower()
        if "reject" in lower or "deni" in lower or "declined" in lower:
            return ("rejected", output_text[:500])
        if (
            lower.startswith("error")
            or "failed" in lower
            or "could not" in lower
            or "no such file" in lower
        ):
            return ("errored", output_text[:500])
        return ("applied", "")

    if isinstance(response, str):
        lower = response.lower()
        if "error" in lower or "failed" in lower:
            return ("errored", response[:500])
        if "reject" in lower or "deni" in lower:
            return ("rejected", response[:500])
        return ("applied", "")

    return ("applied", "")


async def _store_gemini_pending_edits(
    session_key: str,
    function_calls: list[dict],
    prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> None:
    """Parse Gemini functionCalls into edit records and store as pending."""
    import time
    now = time.time()
    ctx = prompt_context or {}
    async with _pending_edits_lock:
        if len(_pending_edits) >= _PENDING_EDITS_MAX:
            to_drop = len(_pending_edits) - _PENDING_EDITS_MAX + len(function_calls) + 1
            for key in list(_pending_edits.keys())[:to_drop]:
                _pending_edits.pop(key, None)

        for fc in function_calls:
            edits = _parse_gemini_function_call_to_edits(fc)
            if not edits:
                continue
            synthetic_id = edits[0]["tool_use_id"]
            _annotate_edits(edits, ctx, routing_info, now)
            _pending_edits[(session_key, synthetic_id)] = edits
            logger.debug(
                "pending gemini edit: id=%s tool=%s file=%s",
                synthetic_id, edits[0].get("tool_name", ""), edits[0].get("file_path", ""),
            )


async def _resolve_gemini_pending_edits(
    session_key: str,
    function_responses: list[dict],
    provider: str,
) -> None:
    """Match functionResponses to pending edits and flush to backend.

    Resolution strategy:
      1. If the functionResponse has an `id` field, look up by exact synthetic_id.
      2. Otherwise, FIFO name-matching on insertion-ordered dict.
    """
    resolved: list[tuple[dict, str, str]] = []
    async with _pending_edits_lock:
        for fr in function_responses:
            response_name = fr.get("name", "")
            response_id = fr.get("id", "")

            matched_key: tuple[str, str] | None = None

            if response_id and isinstance(response_id, str):
                candidate = (session_key, response_id)
                if candidate in _pending_edits:
                    matched_key = candidate

            if matched_key is None:
                name_matches = [
                    key
                    for key in _pending_edits.keys()
                    if key[0] == session_key
                    and _pending_edits[key]
                    and _pending_edits[key][0].get("tool_name") == response_name
                ]
                if len(name_matches) > 1:
                    # Ambiguous: two+ pending calls share this tool name and the
                    # functionResponse carried no `id`, so FIFO may attribute the
                    # status to the wrong pending call (CODE-03). Pick FIFO
                    # (oldest) but log so the mis-attribution is observable.
                    logger.warning(
                        "gemini: %d pending edits match tool=%s without a response id; "
                        "resolving FIFO (status may be mis-attributed)",
                        len(name_matches), response_name,
                    )
                if name_matches:
                    matched_key = name_matches[0]

            if matched_key is None:
                continue

            status, error_message = _classify_gemini_function_response(fr)
            for edit in _pending_edits.pop(matched_key):
                resolved.append((edit, status, error_message))

    ingest_fn = _get_ingest_fn()
    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            ingest_fn(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
