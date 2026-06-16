"""OpenAI /v1/chat/completions adapter for the LineageLens proxy.

Captures AI-generated code from *every* tool that speaks the Chat Completions
format, regardless of how it expresses an edit:

  A. Tool-call edits   — choices[].message.tool_calls[] (and the legacy singular
                         message.function_call), streaming or not.
  B. Text-content edits — choices[].message.content holding Aider SEARCH/REPLACE
                         blocks, unified diffs, apply-patch DSL, or fenced code
                         blocks with a filename hint.
  C. Mixed / fallback  — a single response may carry both; n>1 choices are each
                         captured; if nothing structured is found the raw text
                         still flows through the proxy's generic text fallback.

This is what lets Aider, Cline, Continue, Copilot CLI, Goose, Windsurf and any
OpenAI-compatible backend (together / groq / fireworks / mistral / Azure) be
fully captured rather than falling through to lossy text-only capture.

Everything is fail-open: a parse error never raises into the forwarding path.
"""
import asyncio
import hashlib
import json
import logging
import re
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
from adapters.codex import _parse_apply_patch_dsl

logger = logging.getLogger("lineagelens-proxy")


# ── Path / provider detection ─────────────────────────────────────────────────

def _is_openai_chat_path(url: str) -> bool:
    """Return True iff the upstream URL targets a Chat Completions endpoint.

    Matches both the canonical OpenAI path (/v1/chat/completions) and the Azure
    OpenAI deployment shape (/openai/deployments/{deployment}/chat/completions),
    which carries the same request/response format.
    """
    try:
        path = urllib.parse.urlparse(url).path
    except Exception:
        return False
    return "/chat/completions" in path

# TODO(bedrock/vertex): Claude-via-Bedrock uses /model/{id}/invoke[-with-response-stream]
# on *.bedrock-runtime.*.amazonaws.com and Gemini-via-Vertex uses
# :generateContent / :streamGenerateContent on *-aiplatform.googleapis.com.
# Those are NOT chat/completions and are NOT captured here — they need their own
# path detection + adapters. Capture is deliberately *not* assumed for them.


def _content_to_text(content) -> str:
    """Normalise a chat message `content` (str or list-of-parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text", "") or "")
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts)
    return ""


def _openai_chat_session_key(body_dict: dict, headers: dict) -> str:
    """Stable per-session fingerprint from the system message + auth prefix."""
    system = ""
    for msg in (body_dict.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") in ("system", "developer"):
            system = _content_to_text(msg.get("content"))
            break

    auth = ""
    for header_name in ("authorization", "x-api-key", "Authorization", "X-Api-Key"):
        v = headers.get(header_name)
        if v:
            auth = v[:24]
            break

    raw = f"openai-chat|{system[:4096]}|{auth}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _extract_openai_chat_prompt_context(body_dict: dict) -> dict:
    """Pull model + system + user/assistant messages out of a request body."""
    if not isinstance(body_dict, dict):
        return {}
    model = body_dict.get("model", "")
    if not isinstance(model, str):
        model = ""

    system = ""
    safe_messages: list[dict] = []
    for msg in (body_dict.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role in ("system", "developer") and not system:
            system = _content_to_text(msg.get("content"))
            continue
        if role == "tool":  # tool-result echoes — excluded from prompt context
            continue
        safe_messages.append(msg)

    return {
        "model": model,
        "system": system,
        "messages": safe_messages,
    }


# ── Tool-name → argument-shape table (A) ───────────────────────────────────────
#
# Recognition is by argument *shape* first; these name hints only help when the
# shape is ambiguous. Keep this list extensible — new editor tools slot in here.
_EDIT_TOOL_NAME_HINTS = frozenset({
    "write_file", "write_to_file", "create_file", "createfile",
    "edit_file", "replace_in_file", "str_replace_editor", "str_replace",
    "str_replace_based_edit_tool", "apply_patch", "insert", "patch_file",
    "full_file", "overwrite_file",
})

_PATH_KEYS = ("path", "file_path", "filename", "filepath", "file",
              "notebook_path", "target_file", "fileName", "filePath")
_CONTENT_KEYS = ("content", "new_content", "newContent", "text", "file_text",
                 "fileText", "code", "contents", "source")
_OLD_KEYS = ("old_str", "old_string", "oldStr", "oldString", "oldText",
             "old_text", "search", "find")
_NEW_KEYS = ("new_str", "new_string", "newStr", "newString", "newText",
             "new_text", "replace", "replacement")
_DIFF_KEYS = ("diff", "patch", "udiff", "unified_diff", "input")


def _first(args: dict, keys) -> "object | None":
    """Return the first present value among `keys`, or None."""
    for k in keys:
        if k in args and args[k] is not None:
            return args[k]
    return None


def _records_from_dsl(call_id: str, name: str, patch: str) -> list[dict]:
    """Turn an apply-patch DSL blob into per-file edit records."""
    records: list[dict] = []
    for idx, e in enumerate(_parse_apply_patch_dsl(patch)):
        records.append({
            "tool_use_id": call_id,
            "tool_name": name or "apply_patch",
            "edit_index": idx,
            "file_path": e["file_path"],
            "old_string": e["old_string"],
            "new_string": e["new_string"],
            "verb": e["verb"],
            "moved_to": e["moved_to"],
        })
    return records


def _edits_from_tool_args(name: str, args: dict, call_id: str) -> list[dict]:
    """Map one tool call's parsed arguments to edit records by argument shape."""
    # apply-patch DSL embedded in input/patch/diff
    dsl_blob = _first(args, ("input", "patch", "diff"))
    if isinstance(dsl_blob, str) and "*** Begin Patch" in dsl_blob:
        recs = _records_from_dsl(call_id, name, dsl_blob)
        if recs:
            return recs

    path = _first(args, _PATH_KEYS)
    path = path if isinstance(path, str) else ""
    content = _first(args, _CONTENT_KEYS)
    old = _first(args, _OLD_KEYS)
    new = _first(args, _NEW_KEYS)
    diff = _first(args, _DIFF_KEYS)

    def _rec(verb: str, old_s: str, new_s: str, fp: str = "") -> dict:
        return {
            "tool_use_id": call_id,
            "tool_name": name or "openai_tool_call",
            "edit_index": 0,
            "file_path": fp or path or "proxy-capture",
            "old_string": old_s or "",
            "new_string": new_s or "",
            "verb": verb,
            "moved_to": "",
        }

    # {path, old_str, new_str} → string replace
    if path and old is not None and new is not None:
        return [_rec("replace", str(old), str(new))]

    # {path, content} → full-file write
    if path and content is not None:
        return [_rec("write", "", str(content))]

    # {path, diff|patch} → patch
    if isinstance(diff, str) and diff.strip():
        if "*** Begin Patch" in diff:
            recs = _records_from_dsl(call_id, name, diff)
            if recs:
                return recs
        ud = _parse_unified_diff(diff)
        if ud:
            for idx, e in enumerate(ud):
                e["tool_use_id"] = call_id
                e["tool_name"] = name or "openai_tool_call"
                e["edit_index"] = idx
            return ud
        return [_rec("patch", "", diff)]

    return []


def _parse_openai_tool_call_to_edits(tool_call: dict) -> list[dict]:
    """Convert one normalised tool_call ({id,name,arguments}) into edit records.

    `arguments` is a JSON string (OpenAI convention); malformed JSON fails open
    to []. Returns [] for tool calls that do not describe a file edit.
    """
    name = tool_call.get("name", "") or ""
    call_id = tool_call.get("id", "") or ""
    args_raw = tool_call.get("arguments", "")

    if isinstance(args_raw, str):
        try:
            args = json.loads(args_raw) if args_raw.strip() else {}
        except json.JSONDecodeError:
            logger.debug("openai chat: failed to parse tool_call arguments JSON")
            return []
    elif isinstance(args_raw, dict):
        args = args_raw
    else:
        return []

    if not isinstance(args, dict):
        return []
    return _edits_from_tool_args(name, args, call_id)


# ── Text-content edit parsers (B) ──────────────────────────────────────────────

_PATH_LINE_RE = re.compile(r"[\w./\\-]+\.[\w]+$|/")


def _looks_like_path(line: str) -> bool:
    """Heuristic: does this line look like a bare file path?"""
    s = line.strip().strip("`").strip()
    if not s or " " in s:
        return False
    return ("/" in s) or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", s))


def _filename_before(preamble: str) -> str:
    """Find the nearest preceding path-looking line (Aider names the file there)."""
    for line in reversed(preamble.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        if _looks_like_path(stripped):
            return stripped.strip("`").strip()
        # The filename usually sits immediately above the fence; stop at the
        # first non-blank, non-fence line so unrelated prose isn't grabbed.
        break
    return ""


_SR_RE = re.compile(
    r"<{5,}\s*SEARCH\s*\n(.*?)\n={5,}[^\n]*\n(.*?)\n>{5,}\s*REPLACE",
    re.DOTALL,
)


def _parse_aider_search_replace(text: str) -> list[dict]:
    """Parse Aider-style SEARCH/REPLACE blocks into edit records."""
    edits: list[dict] = []
    for idx, m in enumerate(_SR_RE.finditer(text)):
        old = m.group(1)
        new = m.group(2)
        path = _filename_before(text[:m.start()]) or "proxy-capture"
        edits.append({
            "tool_use_id": "",
            "tool_name": "aider_search_replace",
            "edit_index": idx,
            "file_path": path,
            "old_string": old,
            "new_string": new,
            "verb": "replace",
            "moved_to": "",
        })
    return edits


def _strip_diff_prefix(p: str) -> str:
    """Drop a leading a/ or b/ from a unified-diff path."""
    p = p.strip().strip('"')
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


def _parse_unified_diff(text: str) -> list[dict]:
    """Parse a unified diff / `diff --git` blob into per-file edit records."""
    if not isinstance(text, str):
        return []
    has_marker = (
        "diff --git" in text
        or "\n@@" in text or text.startswith("@@")
        or "\n--- " in text or text.startswith("--- ")
    )
    if not has_marker:
        return []

    files: list[dict] = []
    cur: dict | None = None

    def _flush() -> None:
        nonlocal cur
        if cur is not None and (cur["old"] or cur["new"]):
            files.append(cur)
        cur = None

    for line in text.splitlines():
        if line.startswith("--- "):
            _flush()
            cur = {"file_path": _strip_diff_prefix(line[4:]), "old": [], "new": []}
        elif line.startswith("+++ "):
            if cur is None:
                cur = {"file_path": "", "old": [], "new": []}
            p = line[4:].strip()
            if p not in ("/dev/null",):
                cur["file_path"] = _strip_diff_prefix(p)
        elif line.startswith("@@"):
            continue
        elif line.startswith((
            "diff --git", "index ", "new file", "deleted file",
            "rename ", "similarity ", "old mode", "new mode", "\\ No newline",
        )):
            continue
        elif cur is not None:
            if line.startswith("+"):
                cur["new"].append(line[1:])
            elif line.startswith("-"):
                cur["old"].append(line[1:])
            elif line.startswith(" "):
                cur["old"].append(line[1:])
                cur["new"].append(line[1:])

    _flush()

    edits: list[dict] = []
    for idx, f in enumerate(files):
        edits.append({
            "tool_use_id": "",
            "tool_name": "unified_diff",
            "edit_index": idx,
            "file_path": f["file_path"] or "proxy-capture",
            "old_string": "\n".join(f["old"]),
            "new_string": "\n".join(f["new"]),
            "verb": "update",
            "moved_to": "",
        })
    return edits


_FENCE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)
_FILE_HINT_RE = re.compile(
    r"^\s*(?:#|//|/\*|--|;|<!--)?\s*(?:file|path)\s*[:=]\s*(.+?)\s*(?:\*/|-->)?\s*$",
    re.IGNORECASE,
)


def _path_from_fence_info(info: str) -> str:
    """Extract a path from a fence info string like `python path=app/foo.py`."""
    info = info.strip()
    if not info:
        return ""
    m = re.search(r"(?:path|file)\s*=\s*([^\s]+)", info)
    if m:
        return m.group(1).strip()
    for tok in info.split():
        if "/" in tok or re.fullmatch(r"[\w.\-/]+\.[A-Za-z0-9]{1,8}", tok):
            return tok
    return ""


def _path_from_first_line(code: str) -> str:
    """Extract a path from a leading `# file: app/foo.py` comment."""
    first = code.splitlines()[0] if code else ""
    m = _FILE_HINT_RE.match(first)
    if m:
        return m.group(1).strip().strip("`")
    return ""


def _parse_fenced_code_blocks(text: str) -> list[dict]:
    """Capture fenced code blocks, resolving a path hint where possible.

    A block with no derivable path is still captured as an unattributed AI code
    record (file_path="proxy-capture") so it is never silently dropped.
    """
    edits: list[dict] = []
    idx = 0
    for m in _FENCE_RE.finditer(text):
        info = m.group(1)
        code = m.group(2)
        if not code.strip():
            continue
        path = (
            _path_from_fence_info(info)
            or _path_from_first_line(code)
            or _filename_before(text[:m.start()])
            or "proxy-capture"
        )
        edits.append({
            "tool_use_id": "",
            "tool_name": "text_codeblock",
            "edit_index": idx,
            "file_path": path,
            "old_string": "",
            "new_string": code.rstrip("\n"),
            "verb": "add" if path != "proxy-capture" else "snippet",
            "moved_to": "",
        })
        idx += 1
    return edits


def _parse_text_content_to_edits(text: str) -> list[dict]:
    """Run all text-content edit parsers (B) over assistant text.

    Order matters: structured formats (apply-patch DSL, Aider SEARCH/REPLACE,
    unified diff) are tried first; the generic fenced-block capture only runs
    when none matched, so a single edit is never recorded twice.
    """
    if not text or not text.strip():
        return []

    edits: list[dict] = []
    if "*** Begin Patch" in text:
        for idx, e in enumerate(_parse_apply_patch_dsl(text)):
            edits.append({
                "tool_use_id": "",
                "tool_name": "apply_patch_text",
                "edit_index": idx,
                "file_path": e["file_path"],
                "old_string": e["old_string"],
                "new_string": e["new_string"],
                "verb": e["verb"],
                "moved_to": e["moved_to"],
            })

    edits.extend(_parse_aider_search_replace(text))
    edits.extend(_parse_unified_diff(text))

    if not edits:
        edits.extend(_parse_fenced_code_blocks(text))

    return edits


# ── Response extraction (normalised "choices") ─────────────────────────────────

def _normalise_message_tool_calls(msg: dict, choice_index: int) -> list[dict]:
    """Pull tool_calls + the legacy singular function_call out of a message."""
    tcs: list[dict] = []
    for tc in (msg.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        tcs.append({
            "id": tc.get("id", "") or f"toolcall_{choice_index}_{len(tcs)}",
            "name": fn.get("name", "") or "",
            "arguments": fn.get("arguments", "") or "",
        })
    fc = msg.get("function_call")
    if isinstance(fc, dict) and (fc.get("name") or fc.get("arguments")):
        tcs.append({
            "id": f"fc_{choice_index}",
            "name": fc.get("name", "") or "",
            "arguments": fc.get("arguments", "") or "",
        })
    return tcs


def _extract_openai_choices_from_body(body: bytes) -> list[dict]:
    """Normalise a non-streaming Chat Completions body to [{index,content,tool_calls}]."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    choices = data.get("choices")
    if not isinstance(choices, list):
        return []

    result: list[dict] = []
    for ch in choices:
        if not isinstance(ch, dict):
            continue
        index = ch.get("index", 0)
        msg = ch.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        result.append({
            "index": index,
            "content": _content_to_text(msg.get("content")),
            "tool_calls": _normalise_message_tool_calls(msg, index),
        })
    return result


def _extract_openai_choices_from_sse(chunks: list[bytes]) -> list[dict]:
    """Reassemble streamed Chat Completions deltas into normalised choices.

    Content and tool-call argument fragments are accumulated per choice index
    (and per tool_calls[].index) across SSE chunks before parsing.
    """
    content_by_choice: dict[int, str] = {}
    tools_by_choice: dict[int, dict[int, dict]] = {}
    legacy_by_choice: dict[int, dict] = {}

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

        for choice in (event.get("choices") or []):
            if not isinstance(choice, dict):
                continue
            ci = choice.get("index", 0)
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            c = delta.get("content")
            if isinstance(c, str) and c:
                content_by_choice[ci] = content_by_choice.get(ci, "") + c
            elif isinstance(c, list):
                content_by_choice[ci] = content_by_choice.get(ci, "") + _content_to_text(c)

            for tc in (delta.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                ti = tc.get("index", 0)
                bucket = tools_by_choice.setdefault(ci, {})
                entry = bucket.setdefault(ti, {"id": "", "name": "", "args": []})
                if tc.get("id"):
                    entry["id"] = tc["id"]
                fn = tc.get("function") or {}
                if isinstance(fn, dict):
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    a = fn.get("arguments")
                    if isinstance(a, str) and a:
                        entry["args"].append(a)

            fc = delta.get("function_call")
            if isinstance(fc, dict):
                e = legacy_by_choice.setdefault(ci, {"name": "", "args": []})
                if fc.get("name"):
                    e["name"] = fc["name"]
                a = fc.get("arguments")
                if isinstance(a, str) and a:
                    e["args"].append(a)

    indices = set(content_by_choice) | set(tools_by_choice) | set(legacy_by_choice)
    result: list[dict] = []
    for ci in sorted(indices):
        tcs: list[dict] = []
        for ti in sorted(tools_by_choice.get(ci, {})):
            ent = tools_by_choice[ci][ti]
            tcs.append({
                "id": ent["id"] or f"toolcall_{ci}_{ti}",
                "name": ent["name"],
                "arguments": "".join(ent["args"]),
            })
        if ci in legacy_by_choice:
            ent = legacy_by_choice[ci]
            tcs.append({
                "id": f"fc_{ci}",
                "name": ent["name"],
                "arguments": "".join(ent["args"]),
            })
        result.append({
            "index": ci,
            "content": content_by_choice.get(ci, ""),
            "tool_calls": tcs,
        })
    return result


# ── Tool-result resolution (D) ─────────────────────────────────────────────────

def _extract_openai_tool_results(body: bytes) -> list[dict]:
    """Extract role:"tool" result messages (tool_call_id + content) from a request."""
    try:
        data = json.loads(body)
    except Exception:
        return []
    messages = data.get("messages")
    if not isinstance(messages, list):
        return []
    results: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        tcid = msg.get("tool_call_id", "")
        if not tcid:
            continue
        results.append({
            "tool_call_id": tcid,
            "content": _content_to_text(msg.get("content")),
        })
    return results


def _classify_openai_tool_result(result: dict) -> tuple[str, str]:
    """Map a tool-result message to (status, error_message)."""
    content = result.get("content", "")
    if not isinstance(content, str):
        content = str(content) if content else ""
    lower = content.lower()
    if "reject" in lower or "deni" in lower or "declined" in lower or "permission" in lower:
        return ("rejected", content[:500])
    if (
        lower.startswith("error")
        or "failed" in lower
        or "could not" in lower
        or "no such file" in lower
        or "not found" in lower
        or "did not match" in lower
    ):
        return ("errored", content[:500])
    return ("applied", "")


# ── Pending-store + capture orchestration ──────────────────────────────────────

async def _store_openai_pending_edits(
    session_key: str,
    tool_calls: list[dict],
    prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> int:
    """Parse tool_calls into edit records, store as pending. Returns count stored."""
    import time
    now = time.time()
    ctx = prompt_context or {}
    stored = 0
    async with _pending_edits_lock:
        if len(_pending_edits) >= _PENDING_EDITS_MAX:
            to_drop = len(_pending_edits) - _PENDING_EDITS_MAX + len(tool_calls) + 1
            for key in list(_pending_edits.keys())[:to_drop]:
                _pending_edits.pop(key, None)

        for tc in tool_calls:
            edits = _parse_openai_tool_call_to_edits(tc)
            if not edits:
                continue
            call_id = tc.get("id", "")
            if not call_id:
                continue
            _annotate_edits(edits, ctx, routing_info, now)
            _pending_edits[(session_key, call_id)] = edits
            stored += 1
            logger.debug(
                "pending openai-chat edit: call_id=%s verb=%s file=%s",
                call_id, edits[0].get("verb", ""), edits[0].get("file_path", ""),
            )
    return stored


async def _ingest_openai_text_edits(
    session_key: str,
    text_edits: list[dict],
    prompt_context: dict | None,
    routing_info: dict | None,
    provider: str,
) -> None:
    """Ingest text-content edits directly.

    Text edits carry no tool_call_id, so there is no later result message to
    resolve them against — they are flushed immediately as "applied".
    """
    import time
    now = time.time()
    _annotate_edits(text_edits, prompt_context or {}, routing_info, now)
    ingest_fn = _get_ingest_fn()
    for edit in text_edits:
        task = asyncio.create_task(
            ingest_fn(edit, session_key, "applied", "", provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


async def _capture_openai_chat_from_choices(
    session_key: str,
    choices: list[dict],
    prompt_context: dict | None,
    routing_info: dict | None,
    provider: str,
) -> bool:
    """Run A + B over normalised choices. Returns True iff anything was captured.

    Tool-call edits (A) and text-content edits (B) are both processed for every
    choice — one never suppresses the other (covers the mixed-response case and
    n>1 choices).
    """
    tool_calls_flat: list[dict] = []
    text_edits: list[dict] = []
    for ch in choices:
        for tc in ch.get("tool_calls") or []:
            tool_calls_flat.append(tc)
        content = ch.get("content") or ""
        if content:
            text_edits.extend(_parse_text_content_to_edits(content))

    captured = False
    if tool_calls_flat:
        stored = await _store_openai_pending_edits(
            session_key, tool_calls_flat, prompt_context, routing_info
        )
        if stored:
            captured = True
    if text_edits:
        await _ingest_openai_text_edits(
            session_key, text_edits, prompt_context, routing_info, provider
        )
        captured = True
    return captured


async def _capture_openai_chat_from_body(
    session_key: str,
    body: bytes,
    prompt_context: dict | None,
    routing_info: dict | None,
    provider: str,
) -> bool:
    """Capture from a non-streaming Chat Completions response body."""
    choices = _extract_openai_choices_from_body(body)
    if not choices:
        return False
    return await _capture_openai_chat_from_choices(
        session_key, choices, prompt_context, routing_info, provider
    )


async def _capture_openai_chat_from_sse(
    session_key: str,
    chunks: list[bytes],
    prompt_context: dict | None,
    routing_info: dict | None,
    provider: str,
) -> bool:
    """Capture from a streamed Chat Completions SSE response."""
    choices = _extract_openai_choices_from_sse(chunks)
    if not choices:
        return False
    return await _capture_openai_chat_from_choices(
        session_key, choices, prompt_context, routing_info, provider
    )


async def _resolve_openai_pending_edits(
    session_key: str,
    tool_results: list[dict],
    provider: str,
) -> None:
    """Match tool-result messages to pending edits and flush to backend."""
    resolved: list[tuple[dict, str, str]] = []
    async with _pending_edits_lock:
        for result in tool_results:
            call_id = result.get("tool_call_id", "")
            if not call_id:
                continue
            key = (session_key, call_id)
            if key not in _pending_edits:
                continue
            status, error_message = _classify_openai_tool_result(result)
            for edit in _pending_edits.pop(key):
                resolved.append((edit, status, error_message))

    ingest_fn = _get_ingest_fn()
    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            ingest_fn(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
