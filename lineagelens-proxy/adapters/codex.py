"""OpenAI Codex CLI (Responses API) adapter for the LineageLens proxy."""
import asyncio
import hashlib
import json
import logging
import urllib.parse

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

logger = logging.getLogger("lineagelens-proxy")


def _is_codex_responses_path(url: str) -> bool:
    """Return True iff the upstream URL targets OpenAI's Responses API."""
    try:
        return "/v1/responses" in urllib.parse.urlparse(url).path
    except Exception:
        return False


def _codex_session_key(body_dict: dict, headers: dict) -> str:
    """Stable session fingerprint for OpenAI Responses API."""
    instructions = body_dict.get("instructions", "")
    if not isinstance(instructions, str):
        instructions = ""

    if not instructions:
        for item in (body_dict.get("input") or []):
            if not isinstance(item, dict):
                continue
            role = item.get("role", "")
            if role in ("system", "developer"):
                content = item.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict):
                            parts.append(c.get("text", "") or "")
                    instructions = "".join(parts)
                elif isinstance(content, str):
                    instructions = content
                break

    auth = ""
    for header_name in ("authorization", "x-api-key", "Authorization", "X-Api-Key"):
        v = headers.get(header_name)
        if v:
            auth = v[:24]
            break

    raw = f"codex|{instructions[:4096]}|{auth}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _parse_apply_patch_dsl(patch: str) -> list[dict]:
    """Parse Codex CLI's apply_patch DSL into per-file edit records."""
    if not isinstance(patch, str) or not patch.strip():
        return []

    lines = patch.splitlines()
    files: list[dict] = []
    current: dict | None = None

    def _flush() -> None:
        nonlocal current
        if current is not None:
            files.append(current)
            current = None

    for line in lines:
        if line.startswith("*** Begin Patch") or line.startswith("*** End Patch"):
            continue

        if line.startswith("*** Add File:"):
            _flush()
            current = {
                "verb": "add",
                "file_path": line[len("*** Add File:"):].strip(),
                "moved_to": "",
                "old_lines": [],
                "new_lines": [],
            }
        elif line.startswith("*** Update File:"):
            _flush()
            current = {
                "verb": "update",
                "file_path": line[len("*** Update File:"):].strip(),
                "moved_to": "",
                "old_lines": [],
                "new_lines": [],
            }
        elif line.startswith("*** Delete File:"):
            _flush()
            current = {
                "verb": "delete",
                "file_path": line[len("*** Delete File:"):].strip(),
                "moved_to": "",
                "old_lines": [],
                "new_lines": [],
            }
        elif line.startswith("*** Move to:") and current is not None:
            current["moved_to"] = line[len("*** Move to:"):].strip()
        elif current is not None:
            if line.startswith("@@"):
                continue
            if line.startswith("+"):
                current["new_lines"].append(line[1:])
            elif line.startswith("-"):
                current["old_lines"].append(line[1:])
            elif line.startswith(" "):
                current["old_lines"].append(line[1:])
                current["new_lines"].append(line[1:])

    _flush()

    edits: list[dict] = []
    for f in files:
        edits.append({
            "verb": f["verb"],
            "file_path": f["file_path"],
            "moved_to": f["moved_to"],
            "old_string": "\n".join(f["old_lines"]),
            "new_string": "\n".join(f["new_lines"]),
        })
    return edits


def _parse_codex_function_call_to_edits(function_call: dict) -> list[dict]:
    """Convert one Codex function_call item into edit records."""
    name = function_call.get("name", "")
    if name == "shell":
        logger.debug(
            "codex: shell tool detected, not attributed (call_id=%s)",
            function_call.get("call_id", ""),
        )
        return []
    if name != "apply_patch":
        return []

    call_id = function_call.get("call_id", "") or function_call.get("id", "")
    args_raw = function_call.get("arguments", "")

    if isinstance(args_raw, str):
        try:
            args = json.loads(args_raw) if args_raw else {}
        except json.JSONDecodeError:
            logger.debug("codex: failed to parse apply_patch arguments JSON")
            return []
    elif isinstance(args_raw, dict):
        args = args_raw
    else:
        return []

    if not isinstance(args, dict):
        return []
    patch = args.get("input", "")
    if not isinstance(patch, str):
        return []

    file_edits = _parse_apply_patch_dsl(patch)
    records: list[dict] = []
    for idx, e in enumerate(file_edits):
        records.append({
            "tool_use_id": call_id,
            "tool_name": "apply_patch",
            "edit_index": idx,
            "file_path": e["file_path"],
            "old_string": e["old_string"],
            "new_string": e["new_string"],
            "verb": e["verb"],
            "moved_to": e["moved_to"],
        })
    return records


def _extract_codex_function_calls_from_body(body: bytes) -> list[dict]:
    """Extract function_call items from a non-streaming Responses API body."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    output = data.get("output")
    if not isinstance(output, list):
        return []
    return [
        item for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]


def _extract_codex_function_calls_from_sse(chunks: list[bytes]) -> list[dict]:
    """Assemble function_call items from a Responses API SSE stream."""
    calls_by_index: dict[int, dict] = {}
    args_deltas_by_index: dict[int, list[str]] = {}

    raw = "".join(c.decode("utf-8", errors="replace") for c in chunks)
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        if not line.startswith(_SSE_DATA_PREFIX):
            continue
        payload = line[5:].strip()
        if payload in ("", _SSE_DONE_MARKER):
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")
        idx = event.get("output_index", 0)

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "function_call":
                calls_by_index[idx] = {
                    "type": "function_call",
                    "id": item.get("id", ""),
                    "call_id": item.get("call_id", "") or item.get("id", ""),
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "") or "",
                }
                args_deltas_by_index[idx] = []

        elif etype == "response.function_call_arguments.delta":
            if idx in args_deltas_by_index:
                d = event.get("delta", "")
                if isinstance(d, str) and d:
                    args_deltas_by_index[idx].append(d)

        elif etype == "response.function_call_arguments.done":
            if idx in calls_by_index:
                final = event.get("arguments", "")
                if isinstance(final, str) and final:
                    calls_by_index[idx]["arguments"] = final
                    args_deltas_by_index[idx] = []

        elif etype == "response.output_item.done":
            if idx in calls_by_index:
                item = event.get("item") or {}
                if isinstance(item, dict):
                    a = item.get("arguments", "")
                    if isinstance(a, str) and a:
                        calls_by_index[idx]["arguments"] = a
                if not calls_by_index[idx]["arguments"] and args_deltas_by_index.get(idx):
                    calls_by_index[idx]["arguments"] = "".join(args_deltas_by_index[idx])

    for idx, call in calls_by_index.items():
        if not call["arguments"] and args_deltas_by_index.get(idx):
            call["arguments"] = "".join(args_deltas_by_index[idx])

    return list(calls_by_index.values())


def _extract_codex_function_call_outputs(body: bytes) -> list[dict]:
    """Extract function_call_output items from a Responses API request body."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    inputs = data.get("input")
    if not isinstance(inputs, list):
        return []
    return [
        item for item in inputs
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]


def _extract_codex_prompt_context(body_dict: dict) -> dict:
    """Pull model + instructions + user messages out of a Codex request body."""
    if not isinstance(body_dict, dict):
        return {}
    model = body_dict.get("model", "")
    if not isinstance(model, str):
        model = ""

    instructions = body_dict.get("instructions", "")
    if not isinstance(instructions, str):
        instructions = ""

    inputs = body_dict.get("input")
    safe_messages: list[dict] = []
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type in ("function_call", "function_call_output"):
                continue
            safe_messages.append(item)

    return {
        "model": model,
        "system": instructions,
        "messages": safe_messages,
    }


def _classify_codex_function_call_output(item: dict) -> tuple[str, str]:
    """Map a Codex function_call_output to (status, error_message)."""
    output = item.get("output", "")
    if isinstance(output, dict):
        if "error" in output:
            return ("errored", str(output["error"])[:500])
        output = output.get("text", "") or output.get("content", "") or ""
    if not isinstance(output, str):
        output = str(output) if output else ""

    lower = output.lower()
    if "reject" in lower or "deni" in lower or "declined" in lower or "user " in lower:
        return ("rejected", output[:500])
    if (
        lower.startswith("error")
        or "failed" in lower
        or "could not" in lower
        or "no such file" in lower
        or "not found" in lower
        or "did not match" in lower
    ):
        return ("errored", output[:500])
    return ("applied", "")


async def _store_codex_pending_edits(
    session_key: str,
    function_calls: list[dict],
    prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> None:
    """Parse Codex function_calls into edit records and store as pending."""
    import time
    now = time.time()
    ctx = prompt_context or {}
    async with _pending_edits_lock:
        if len(_pending_edits) >= _PENDING_EDITS_MAX:
            to_drop = len(_pending_edits) - _PENDING_EDITS_MAX + len(function_calls) + 1
            for key in list(_pending_edits.keys())[:to_drop]:
                _pending_edits.pop(key, None)

        for fc in function_calls:
            edits = _parse_codex_function_call_to_edits(fc)
            if not edits:
                continue
            call_id = fc.get("call_id", "") or fc.get("id", "")
            if not call_id:
                continue
            _annotate_edits(edits, ctx, routing_info, now)
            _pending_edits[(session_key, call_id)] = edits
            logger.debug(
                "pending codex edit: call_id=%s verb=%s file=%s",
                call_id, edits[0].get("verb", ""), edits[0].get("file_path", ""),
            )


async def _resolve_codex_pending_edits(
    session_key: str,
    function_call_outputs: list[dict],
    provider: str,
) -> None:
    """Match function_call_outputs to pending edits and flush to backend."""
    resolved: list[tuple[dict, str, str]] = []
    async with _pending_edits_lock:
        for output_item in function_call_outputs:
            call_id = output_item.get("call_id", "")
            if not call_id:
                continue
            key = (session_key, call_id)
            if key not in _pending_edits:
                continue
            status, error_message = _classify_codex_function_call_output(output_item)
            for edit in _pending_edits.pop(key):
                resolved.append((edit, status, error_message))

    ingest_fn = _get_ingest_fn()
    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            ingest_fn(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
