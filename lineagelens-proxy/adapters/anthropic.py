"""Anthropic tool_use / tool_result adapter for the LineageLens proxy."""
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime

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

# Claude Code's file-mutating tools (captured as code provenance).
_FILE_MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Read-only tools that produce no side-effects — skip from action ledger.
_READ_ONLY_TOOLS = {
    "Read", "Glob", "Grep", "LS", "Find", "Search",
    "ReadFile", "ListDir", "ListFiles",
}

# Shell/bash execution tools.
_SHELL_TOOLS = {"Bash", "Shell", "bash", "shell", "Execute", "RunCommand", "execute_command"}

# File-deletion and move tools.
_FILE_DELETE_TOOLS = {"Remove", "Delete", "DeleteFile", "Trash", "Unlink"}

# Outbound network tools.
_NETWORK_TOOLS = {"WebFetch", "Fetch", "curl", "wget", "HttpRequest", "http_request"}

# Package/dependency install command fragments (matched against Bash commands).
_INSTALL_FRAGMENTS = (
    "npm install", "npm i ", "yarn add", "pnpm add",
    "pip install", "pip3 install", "uv add", "poetry add",
    "cargo install", "gem install", "go get",
    "apt install", "apt-get install", "brew install", "apk add",
)

# Max bytes for a single argument string value sent to the backend.
_ARG_VALUE_MAX = 1024


def _compute_prompt_context_id(prompt_context: dict) -> str:
    """Stable 32-char identifier for the originating prompt context.

    Hashes model name + system prompt prefix so it can link action rows back
    to the ProvenanceRecord chain without embedding raw prompt content.
    """
    canonical = json.dumps({
        "model": (prompt_context.get("model") or "")[:128],
        "system": (prompt_context.get("system") or "")[:512],
    }, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _redact_arg_value(value: object) -> object:
    """Apply secret redaction to a single argument value (recursive)."""
    from ingest import _redact, _redact_value  # local import avoids circular at module load
    return _redact_value(value)


def _bound_arg(value: object) -> object:
    """Truncate long strings inside an argument value."""
    if isinstance(value, str):
        return value[:_ARG_VALUE_MAX]
    if isinstance(value, list):
        return [_bound_arg(v) for v in value]
    if isinstance(value, dict):
        return {k: _bound_arg(v) for k, v in value.items()}
    return value


def _sanitise_args(inp: dict) -> dict:
    """Redact secrets and size-bound all argument values."""
    bounded = {k: _bound_arg(v) for k, v in inp.items()}
    return _redact_arg_value(bounded)


def _classify_agent_action_type(tool_name: str, inp: dict) -> str | None:
    """Return the action_type for this tool_use or None to skip it.

    None is returned for read-only tools (Read, Glob, Grep, …) which have no
    side-effects and would only add noise to the action ledger.
    """
    if tool_name in _READ_ONLY_TOOLS:
        return None

    # File-mutating tools are already ingested as code provenance; we still
    # record them in the action ledger so the session timeline is complete.
    if tool_name in _FILE_MUTATING_TOOLS:
        return "file_write"

    if tool_name in _FILE_DELETE_TOOLS:
        return "file_delete"

    if tool_name in _NETWORK_TOOLS:
        return "network"

    if tool_name in _SHELL_TOOLS:
        cmd = inp.get("command", "") or inp.get("cmd", "") or ""
        if isinstance(cmd, str):
            cmd_lower = cmd.lower()
            if any(frag in cmd_lower for frag in _INSTALL_FRAGMENTS):
                return "dependency_install"
        return "shell"

    # Everything else that is not a known read-only tool.
    return "other"


def _extract_anthropic_agent_actions(
    tool_uses: list[dict],
    prompt_context: dict,
    session_key: str,
) -> list[dict]:
    """Convert raw tool_use blocks into agent-action ingest dicts.

    Returns a list ready to POST to /agent-actions.  Read-only tools are
    silently dropped.  All argument values are size-bounded and run through
    secret redaction before being included.
    """
    now_iso = datetime.now(tz=UTC).isoformat()
    result: list[dict] = []

    for tool_use in tool_uses:
        tool_name = tool_use.get("name", "")
        if not tool_name:
            continue

        inp = tool_use.get("input") or {}
        if not isinstance(inp, dict):
            inp = {}

        action_type = _classify_agent_action_type(tool_name, inp)
        if action_type is None:
            continue

        try:
            safe_args = _sanitise_args(inp)
        except Exception:
            safe_args = {}

        result.append({
            "actionType": action_type,
            "toolName": tool_name,
            "argumentsJson": safe_args or None,
            "occurredAt": now_iso,
        })

    return result


def _session_key(body_dict: dict, headers: dict) -> str:
    """Stable per-session fingerprint from system prompt + auth prefix."""
    system = body_dict.get("system", "")
    if isinstance(system, list):
        system = "".join(
            (s.get("text", "") if isinstance(s, dict) else str(s)) for s in system
        )
    elif not isinstance(system, str):
        system = str(system)

    auth = ""
    for header_name in ("authorization", "x-api-key", "Authorization", "X-Api-Key"):
        v = headers.get(header_name)
        if v:
            auth = v[:24]
            break

    raw = f"{system[:4096]}|{auth}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _parse_anthropic_tool_use_to_edits(tool_use: dict) -> list[dict]:
    """Convert one Anthropic tool_use block into one or more edit records.

    Returns [] for non-file-mutating tools (Bash, Read, Glob, etc.).
    MultiEdit produces multiple records sharing the same tool_use_id.
    """
    name = tool_use.get("name", "")
    if name not in _FILE_MUTATING_TOOLS:
        return []

    tool_use_id = tool_use.get("id", "")
    inp = tool_use.get("input") or {}
    if not isinstance(inp, dict):
        return []

    if name == "Edit":
        return [{
            "tool_use_id": tool_use_id,
            "tool_name": "Edit",
            "edit_index": 0,
            "file_path": inp.get("file_path", ""),
            "old_string": inp.get("old_string", ""),
            "new_string": inp.get("new_string", ""),
        }]

    if name == "Write":
        return [{
            "tool_use_id": tool_use_id,
            "tool_name": "Write",
            "edit_index": 0,
            "file_path": inp.get("file_path", ""),
            "old_string": "",
            "new_string": inp.get("content", ""),
        }]

    if name == "MultiEdit":
        edits = inp.get("edits") or []
        if not isinstance(edits, list):
            return []
        records = []
        for idx, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            records.append({
                "tool_use_id": tool_use_id,
                "tool_name": "MultiEdit",
                "edit_index": idx,
                "file_path": inp.get("file_path", ""),
                "old_string": edit.get("old_string", ""),
                "new_string": edit.get("new_string", ""),
            })
        return records

    if name == "NotebookEdit":
        return [{
            "tool_use_id": tool_use_id,
            "tool_name": "NotebookEdit",
            "edit_index": 0,
            "file_path": inp.get("notebook_path", "") or inp.get("file_path", ""),
            "old_string": "",
            "new_string": inp.get("new_source", ""),
        }]

    return []


def _extract_anthropic_tool_uses_from_body(body: bytes) -> list[dict]:
    """Extract all tool_use content blocks from a non-streaming Anthropic response."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    content = data.get("content")
    if not isinstance(content, list):
        return []
    return [
        block for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _extract_anthropic_tool_uses_from_sse(chunks: list[bytes]) -> list[dict]:
    """Assemble tool_use blocks from a stream of Anthropic SSE chunks."""
    tool_uses_by_index: dict[int, dict] = {}
    input_json_by_index: dict[int, list[str]] = {}

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

        if etype == "content_block_start":
            idx = event.get("index", 0)
            block = event.get("content_block") or {}
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_uses_by_index[idx] = {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": {},
                }
                input_json_by_index[idx] = []

        elif etype == "content_block_delta":
            idx = event.get("index", 0)
            if idx not in input_json_by_index:
                continue
            delta = event.get("delta") or {}
            if isinstance(delta, dict) and delta.get("type") == "input_json_delta":
                partial = delta.get("partial_json", "")
                if partial:
                    input_json_by_index[idx].append(partial)

        elif etype == "content_block_stop":
            idx = event.get("index", 0)
            if idx in tool_uses_by_index:
                assembled = "".join(input_json_by_index.get(idx, []))
                if assembled:
                    try:
                        tool_uses_by_index[idx]["input"] = json.loads(assembled)
                    except json.JSONDecodeError:
                        logger.debug("failed to parse tool_use input JSON: %r", assembled[:200])

    return list(tool_uses_by_index.values())


def _extract_anthropic_tool_results(body: bytes) -> list[dict]:
    """Extract all tool_result blocks from an Anthropic request body's messages."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    messages = data.get("messages")
    if not isinstance(messages, list):
        return []

    results = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results.append(block)
    return results


def _extract_anthropic_prompt_context(body_dict: dict) -> dict:
    """Pull model + system + messages out of an Anthropic request body."""
    if not isinstance(body_dict, dict):
        return {}
    model = body_dict.get("model", "")
    if not isinstance(model, str):
        model = ""

    system = body_dict.get("system", "")
    if isinstance(system, list):
        parts = []
        for s in system:
            if isinstance(s, dict):
                parts.append(s.get("text", "") or "")
            elif isinstance(s, str):
                parts.append(s)
        system = "\n".join(p for p in parts if p)
    elif not isinstance(system, str):
        system = ""

    messages = body_dict.get("messages")
    safe_messages: list[dict] = []
    if isinstance(messages, list):
        for msg in messages:
            if isinstance(msg, dict):
                safe_messages.append(msg)

    return {
        "model": model,
        "system": system,
        "messages": safe_messages,
    }


def _classify_tool_result(result: dict) -> tuple[str, str]:
    """Map a tool_result to (status, error_message)."""
    is_error = bool(result.get("is_error", False))
    content = result.get("content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        content_str = "\n".join(parts)
    else:
        content_str = str(content) if content else ""

    if not is_error:
        return ("applied", "")

    lower = content_str.lower()
    if "reject" in lower or "deni" in lower or "permission" in lower or "user " in lower:
        return ("rejected", content_str[:500])
    return ("errored", content_str[:500])


async def _store_pending_edits(
    session_key: str,
    tool_uses: list[dict],
    prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> None:
    """Convert tool_uses to edit records and stash them as pending.

    Also fires a background task to record non-code agent actions (shell
    commands, network calls, dependency installs, file deletions, …) in the
    action ledger via POST /agent-actions.  Fail-open: action ledger errors
    never disrupt the code-edit capture or proxy forwarding paths.
    """
    import time
    now = time.time()
    ctx = prompt_context or {}
    async with _pending_edits_lock:
        if len(_pending_edits) >= _PENDING_EDITS_MAX:
            to_drop = len(_pending_edits) - _PENDING_EDITS_MAX + len(tool_uses) + 1
            for key in list(_pending_edits.keys())[:to_drop]:
                _pending_edits.pop(key, None)

        for tool_use in tool_uses:
            edits = _parse_anthropic_tool_use_to_edits(tool_use)
            if not edits:
                continue
            tool_use_id = tool_use.get("id", "")
            if not tool_use_id:
                continue
            _annotate_edits(edits, ctx, routing_info, now)
            _pending_edits[(session_key, tool_use_id)] = edits
            logger.debug(
                "pending edit: tool=%s file=%s id=%s",
                edits[0].get("tool_name"), edits[0].get("file_path"), tool_use_id,
            )

    # Fire-and-forget: capture agent actions for ALL tool_uses (not just edits).
    try:
        actions = _extract_anthropic_agent_actions(tool_uses, ctx, session_key)
        if actions:
            prompt_context_id = _compute_prompt_context_id(ctx)
            from ingest import _ingest_agent_actions  # local import — avoids circular at module level
            _task = asyncio.create_task(
                _ingest_agent_actions(actions, session_key, prompt_context_id)
            )
            _background_tasks.add(_task)
            _task.add_done_callback(_background_tasks.discard)
    except Exception:
        logger.debug("agent action extraction failed (non-fatal)", exc_info=True)


async def _resolve_pending_edits(
    session_key: str,
    tool_results: list[dict],
    provider: str,
) -> None:
    """Match tool_results to pending edits; flush resolved edits to backend."""
    resolved: list[tuple[dict, str, str]] = []
    async with _pending_edits_lock:
        for result in tool_results:
            tool_use_id = result.get("tool_use_id", "")
            if not tool_use_id:
                continue
            key = (session_key, tool_use_id)
            if key not in _pending_edits:
                continue
            status, error_message = _classify_tool_result(result)
            for edit in _pending_edits.pop(key):
                resolved.append((edit, status, error_message))

    ingest_fn = _get_ingest_fn()
    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            ingest_fn(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
