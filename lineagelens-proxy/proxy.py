#!/usr/bin/env python3
"""
LineageLens Universal LLM Proxy

Transparently forwards requests to any LLM API (Anthropic, OpenAI, or any
compatible endpoint) and captures AI-generated code into the LineageLens backend.

Works with: Claude Code, Cursor, Copilot, Windsurf, Continue, any CLI or IDE
that supports a configurable base URL.

Setup:
    export ANTHROPIC_BASE_URL=http://localhost:8788   # Claude Code / Anthropic SDK
    export OPENAI_BASE_URL=http://localhost:8788       # OpenAI SDK / compatible tools
"""
import asyncio
import hashlib
import json
import logging
import os
import posixpath
import re
import tempfile
import time
import urllib.parse
import uuid
from http import HTTPStatus
from datetime import UTC, datetime

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from classifier import classify_request
from pricing import estimate_savings
from routing_cache import cancel_refresh_loop, get_policy, init_routing_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lineagelens-proxy")

UPSTREAM_URL      = os.environ.get("UPSTREAM_URL", "https://api.anthropic.com").rstrip("/")
# Per-provider upstream URLs.  When set, requests classified for that provider
# are forwarded to that URL instead of the generic UPSTREAM_URL.  Leave empty to
# keep the original single-upstream behaviour (backward-compatible default).
_ANTHROPIC_UPSTREAM_URL = os.environ.get("ANTHROPIC_UPSTREAM_URL", "").rstrip("/")
_OPENAI_UPSTREAM_URL    = os.environ.get("OPENAI_UPSTREAM_URL",    "").rstrip("/")
_GEMINI_UPSTREAM_URL    = os.environ.get("GEMINI_UPSTREAM_URL",    "").rstrip("/")
BACKEND_URL       = os.environ.get("BACKEND_URL", "http://backend:8787").rstrip("/")
INGEST_TOKEN      = os.environ.get("BACKEND_INGEST_TOKEN", "")
WORKSPACE_ID      = os.environ.get("PROXY_WORKSPACE_ID", "proxy-capture")
PROXY_PORT        = int(os.environ.get("PROXY_PORT", "8788"))
PROXY_HOST        = os.environ.get("PROXY_HOST", "0.0.0.0")
MAX_BODY_BYTES    = int(os.environ.get("PROXY_MAX_BODY_BYTES", "2000000"))
# Comma-separated regex patterns to redact from captured content before ingest.
# Example: PROXY_REDACT_PATTERNS="Bearer [A-Za-z0-9._-]+,sk-[A-Za-z0-9]+"
_REDACT_PATTERNS_RAW  = [p.strip() for p in os.environ.get("PROXY_REDACT_PATTERNS", "").split(",") if p.strip()]
REDACT_PATTERNS: list[re.Pattern] = []
for _rp in _REDACT_PATTERNS_RAW:
    try:
        REDACT_PATTERNS.append(re.compile(_rp))
    except re.error as _rp_err:
        import sys as _sys
        print(f"[lineagelens-proxy] WARNING: ignoring invalid PROXY_REDACT_PATTERNS entry {_rp!r}: {_rp_err}", file=_sys.stderr)
# CONNECT tunnel server (for tools that use HTTPS_PROXY / HTTP_PROXY)
PROXY_CONNECT_PORT    = int(os.environ.get("PROXY_CONNECT_PORT", "8789"))
# Optional CA cert for HTTPS CONNECT MITM. When unset, CONNECT falls back to transparent relay.
PROXY_CA_CERT_PATH    = os.environ.get("PROXY_CA_CERT_PATH", "")
PROXY_CA_KEY_PATH     = os.environ.get("PROXY_CA_KEY_PATH", "")

_background_tasks: set[asyncio.Task] = set()
# Cache of per-host generated certs: hostname -> (cert_pem, key_pem)
# Capped at 500 entries (ordered dict evicts oldest on overflow).
_HOST_CERT_CACHE_MAX = 500
_host_cert_cache: dict[str, tuple[bytes, bytes]] = {}

MAX_RESPONSE_BODY_BYTES = MAX_BODY_BYTES  # reuse same limit for response pre-flight check

# SSE protocol markers — shared across all provider parsers.
_SSE_DATA_PREFIX = "data:"
_SSE_DONE_MARKER = "[DONE]"

# ── Anthropic tool_use / tool_result adapter ───────────────────────────────────
# Pending edits keyed by (session_key, tool_use_id). Each value is the list of
# edit records derived from that tool_use (MultiEdit produces multiple).
_pending_edits: dict[tuple[str, str], list[dict]] = {}
_pending_edits_lock = asyncio.Lock()
_PENDING_EDITS_TTL_SECONDS = 3600  # drop unresolved proposals after 1 hour
_PENDING_EDITS_MAX = 5000  # hard cap to prevent unbounded growth

# Claude Code's file-mutating tools. Other tools (Bash, Read, Glob, Grep, etc.)
# are logged but produce no edit records.
_FILE_MUTATING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def detect_provider_and_format(url: str, headers: dict) -> str:
    """Detect which LLM provider this request targets.

    Checks the destination URL, headers, and request path.
    Returns one of: "anthropic", "openai", "gemini", or "unknown".
    """
    url_lower = url.lower()
    header_keys = {k.lower() for k in headers}

    # Anthropic: direct API or header signature
    if "anthropic.com" in url_lower or "anthropic-version" in header_keys:
        return "anthropic"
    # Gemini
    if "googleapis.com" in url_lower or "generativelanguage.googleapis.com" in url_lower:
        return "gemini"
    # OpenAI official
    if "openai.com" in url_lower:
        return "openai"
    # Azure OpenAI: *.openai.azure.com
    if "openai.azure.com" in url_lower:
        return "openai"
    # Together AI, Groq, Fireworks, Mistral — all speak OpenAI chat format
    for host in ("api.together.xyz", "api.groq.com", "api.fireworks.ai", "api.mistral.ai"):
        if host in url_lower:
            return "openai"
    # Path-based heuristic for generic /v1/messages (Anthropic format) or /v1/chat/completions
    path = urllib.parse.urlparse(url).path
    if "/v1/messages" in path:
        return "anthropic"
    if "/v1/chat/completions" in path:
        return "openai"
    return "unknown"


def detect_provider_from_inbound(path: str, headers: dict) -> str:
    """Detect the target LLM provider from the RAW inbound request path and headers.

    This must be called BEFORE the URL is rewritten to the upstream base so
    the path still reflects how the CLI tool addressed the proxy, not where we
    are forwarding it.

    Returns one of: "anthropic", "openai", "gemini", or "unknown".

    Detection strategy (in priority order):
      1. Anthropic-version or x-api-key-provider:anthropic header  → anthropic
      2. Gemini path pattern (/v1beta/ or /v1/models or gemini keyword) → gemini
      3. OpenAI path (/v1/chat/completions, /v1/responses, /v1/embeddings)  → openai
      4. Anthropic path (/v1/messages)                               → anthropic
      5. Header-based fallback: if Authorization has no goog prefix  → openai
      6. Unknown
    """
    header_keys_lower = {k.lower(): v for k, v in headers.items()}
    path_lower = path.lower()

    # Priority 1: explicit Anthropic marker headers
    if (
        "anthropic-version" in header_keys_lower
        or header_keys_lower.get("x-api-key-provider", "").lower() == "anthropic"
    ):
        return "anthropic"

    # Priority 2: Gemini path patterns — Gemini CLI uses /v1beta/ or /v1/models/<name>:generateContent
    if (
        "/v1beta/" in path_lower
        or ":generatecontent" in path_lower
        or ":streamgeneratecontent" in path_lower
        or "gemini" in path_lower
    ):
        return "gemini"

    # Priority 3: OpenAI path patterns (chat completions, responses API, embeddings)
    if (
        "/v1/chat/completions" in path_lower
        or "/v1/responses" in path_lower
        or "/v1/embeddings" in path_lower
        or "/v1/completions" in path_lower
    ):
        return "openai"

    # Priority 4: Anthropic messages path
    if "/v1/messages" in path_lower:
        return "anthropic"

    # Priority 5: Bearer token heuristic — Google tokens start with "ya29." or are long OAuth
    auth = header_keys_lower.get("authorization", "")
    if auth.lower().startswith("bearer ya29.") or "x-goog" in header_keys_lower:
        return "gemini"

    return "unknown"


def _get_provider_upstream_base(provider: str) -> str:
    """Return the upstream base URL for a detected provider.

    Checks per-provider env vars first; falls back to the generic UPSTREAM_URL
    so existing single-upstream deployments continue to work unchanged.
    """
    if provider == "anthropic" and _ANTHROPIC_UPSTREAM_URL:
        return _ANTHROPIC_UPSTREAM_URL
    if provider == "openai" and _OPENAI_UPSTREAM_URL:
        return _OPENAI_UPSTREAM_URL
    if provider == "gemini" and _GEMINI_UPSTREAM_URL:
        return _GEMINI_UPSTREAM_URL
    return UPSTREAM_URL


def _build_upstream_url_for_provider(provider: str, safe_path: str, raw_query: str) -> str:
    """Build the upstream URL using the provider-specific base URL."""
    base = _get_provider_upstream_base(provider)
    parsed = urllib.parse.urlparse(base)
    upstream_path = parsed.path.rstrip("/") + "/" + safe_path if safe_path else parsed.path
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, upstream_path, "", raw_query, ""))


def extract_file_path(request_headers: dict, request_body: "dict | None") -> str:
    """Resolve a meaningful file path from request metadata.

    Priority:
    1. X-File-Path header
    2. X-Lineage-File header
    3. "File: /path" pattern inside the system prompt
    4. Fallback literal "proxy-capture"
    """
    lower_headers = {k.lower(): v for k, v in request_headers.items()}

    if "x-file-path" in lower_headers:
        return lower_headers["x-file-path"].strip()
    if "x-lineage-file" in lower_headers:
        return lower_headers["x-lineage-file"].strip()

    if request_body:
        system = request_body.get("system", "") or ""
        if isinstance(system, str):
            m = re.search(r'(?:File|file|path):\s*([^\n\r]+)', system)
            if m:
                return m.group(1).strip()

    return "proxy-capture"


def _sanitize_path(path: str) -> str:
    """Normalise a proxy path to prevent directory traversal."""
    return posixpath.normpath("/" + path).lstrip("/")


def _build_upstream_url(safe_path: str, raw_query: str) -> str:
    """Build the upstream URL from the fixed UPSTREAM_URL base and the sanitized path.

    Scheme and netloc always come from the trusted UPSTREAM_URL config value so
    user-supplied path segments can never redirect the request to a different host.
    """
    parsed = urllib.parse.urlparse(UPSTREAM_URL)
    upstream_path = parsed.path.rstrip("/") + "/" + safe_path if safe_path else parsed.path
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, upstream_path, "", raw_query, ""))


def _backend_url_points_to_proxy() -> bool:
    try:
        parsed = urllib.parse.urlparse(BACKEND_URL)
    except Exception:
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    try:
        port = parsed.port
    except ValueError:
        return False

    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    proxy_hosts = {PROXY_HOST.strip().lower(), "localhost", "127.0.0.1", "0.0.0.0", "::1"}
    return port == PROXY_PORT and host in proxy_hosts

_DROP_REQ  = {"host", "content-length", "transfer-encoding", "connection",
              "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
_DROP_RESP = {"content-encoding", "transfer-encoding", "connection", "content-length"}

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup/shutdown context for FastAPI (replaces deprecated on_event)."""
    # Startup: launch pending-edit TTL cleanup.
    task = asyncio.create_task(_cleanup_pending_edits_loop())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    # Startup: initialise routing policy cache (non-blocking; fails gracefully).
    try:
        await init_routing_cache()
    except Exception as _rc_err:
        logger.warning("routing cache init failed (routing disabled until next refresh): %s", _rc_err)
    try:
        yield
    finally:
        # Shutdown: cancel background tasks so the event loop can close cleanly.
        cancel_refresh_loop()
        for t in list(_background_tasks):
            t.cancel()


app = FastAPI(
    title="LineageLens Universal Proxy",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _redact(text: str) -> str:
    """Apply PROXY_REDACT_PATTERNS to text before it is sent to the ingest backend."""
    for pattern in REDACT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _redact_value(value):
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _fwd_headers(h) -> dict:
    forwarded = {k: v for k, v in h.items() if k.lower() not in _DROP_REQ}
    if "authorization" in {k.lower() for k in forwarded}:
        logger.debug("Forwarding Authorization header to upstream — ensure client API keys are intended for the configured upstream URL")
    return forwarded


def _resp_headers(h) -> dict:
    return {k: v for k, v in h.items() if k.lower() not in _DROP_RESP}


def _is_streaming(url: str, body: bytes) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    if ":streamgeneratecontent" in path or "/responses/stream" in path or path.endswith("/stream"):
        return True

    try:
        parsed = json.loads(body)
    except Exception:
        return False

    return bool(isinstance(parsed, dict) and parsed.get("stream", False))


def _text_from_body(body: bytes, provider: str = "unknown") -> str:
    """Extract assistant text from a complete (non-streaming) JSON response."""
    try:
        data = json.loads(body)
    except Exception:
        return ""

    def _try_openai(d: dict) -> str:
        try:
            return d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    def _try_anthropic(d: dict) -> str:
        try:
            parts = d["content"]
            if isinstance(parts, list):
                return "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        except (KeyError, TypeError):
            pass
        return ""

    def _try_gemini(d: dict) -> str:
        try:
            return d["candidates"][0]["content"]["parts"][0]["text"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    if provider == "openai":
        return _try_openai(data)
    if provider == "anthropic":
        return _try_anthropic(data)
    if provider == "gemini":
        return _try_gemini(data)
    return _try_openai(data) or _try_anthropic(data) or _try_gemini(data)


def _delta_from_sse_payload(payload: str, provider: str = "unknown") -> str:
    """Extract the text delta from a single parsed SSE data payload.

    Accepts an explicit *provider* hint ("anthropic", "openai", "gemini", or
    "unknown") so the correct format is tried first.  For "unknown" the
    function falls back through all known formats.

    Returns the assistant text fragment or an empty string.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""

    def _try_anthropic(d: dict) -> str:
        if d.get("type") == "content_block_delta":
            return d.get("delta", {}).get("text", "") or ""
        return ""

    def _try_openai(d: dict) -> str:
        try:
            return d["choices"][0]["delta"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return ""

    def _try_gemini(d: dict) -> str:
        try:
            return d["candidates"][0]["content"]["parts"][0]["text"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    if provider == "anthropic":
        return _try_anthropic(data)
    if provider == "openai":
        return _try_openai(data)
    if provider == "gemini":
        return _try_gemini(data)
    # "unknown": try all formats in order
    return _try_anthropic(data) or _try_openai(data) or _try_gemini(data)


def _text_from_chunk(chunk: bytes, provider: str = "unknown") -> list[str]:
    """Decode one SSE chunk and return all text deltas found in it."""
    try:
        raw = chunk.decode("utf-8", errors="replace")
    except Exception:
        return []
    parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith(_SSE_DATA_PREFIX):
            continue
        payload = line[5:].strip()
        if payload in ("", _SSE_DONE_MARKER):
            continue
        delta = _delta_from_sse_payload(payload, provider)
        if delta:
            parts.append(delta)
    return parts


def _text_from_sse(chunks: list[bytes], provider: str = "unknown") -> str:
    """Reconstruct assistant text from a stream of SSE chunks.

    Carries a remainder across chunk boundaries so a TCP segment split inside a
    data: line never silently drops a text delta.  Accepts a *provider* hint so
    the correct SSE format is parsed (anthropic / openai / gemini / unknown).
    """
    parts: list[str] = []
    remainder = ""
    for chunk in chunks:
        raw = remainder + chunk.decode("utf-8", errors="replace")
        lines = raw.split("\n")
        remainder = lines[-1]
        for line in lines[:-1]:
            line = line.rstrip("\r")
            if not line.startswith(_SSE_DATA_PREFIX):
                continue
            payload = line[5:].strip()
            if payload in ("", _SSE_DONE_MARKER):
                continue
            delta = _delta_from_sse_payload(payload, provider)
            if delta:
                parts.append(delta)
    if remainder:
        line = remainder.rstrip("\r")
        if line.startswith(_SSE_DATA_PREFIX):
            payload = line[5:].strip()
            if payload not in ("", _SSE_DONE_MARKER):
                delta = _delta_from_sse_payload(payload, provider)
                if delta:
                    parts.append(delta)
    return "".join(parts)


def _extract_code(text: str) -> str:
    """Pull out fenced code blocks; fall back to full text if none found."""
    blocks = re.findall(r"```(?:\w+)?\n?(.*?)```", text, re.DOTALL)
    non_empty = [b.strip() for b in blocks if b.strip()]
    if non_empty:
        return "\n\n".join(non_empty)
    return text.strip()


# ── Anthropic tool_use / tool_result parsers ──────────────────────────────────

def _session_key(body_dict: dict, headers: dict) -> str:
    """Stable per-session fingerprint from system prompt + auth prefix.

    Two concurrent Claude Code sessions through the same proxy must not alias
    their tool_use_ids. The system prompt is stable within a session and
    differs between sessions; the auth prefix disambiguates per-user.
    """
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
    """Assemble tool_use blocks from a stream of Anthropic SSE chunks.

    Anthropic streams tool_use as:
      content_block_start  (carries id + name, empty input)
      content_block_delta  (input_json_delta with partial_json fragments)
      content_block_stop
    """
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
    """Pull model + system + messages out of an Anthropic request body.

    Returned dict feeds the backend's promptMessages/modelName fields so the
    dashboard can display 'what was asked and which model answered it'.
    """
    if not isinstance(body_dict, dict):
        return {}
    model = body_dict.get("model", "")
    if not isinstance(model, str):
        model = ""

    system = body_dict.get("system", "")
    if isinstance(system, list):
        # system can also be a list of content blocks in Anthropic
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
    """Map a tool_result to (status, error_message).

    Anthropic's is_error=true covers both harness errors and explicit user
    rejection (e.g. "user denied permission"). We use a simple heuristic on
    the content string to distinguish rejected from errored.
    """
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


def _annotate_edits(
    edits: list[dict],
    ctx: dict,
    routing_info: dict | None,
    proposed_at: float,
) -> None:
    """Shared helper (R1/R2): annotate a list of edit dicts with context metadata in-place."""
    for edit in edits:
        edit["_proposed_at"] = proposed_at
        edit["_model"] = ctx.get("model", "")
        edit["_system"] = ctx.get("system", "")
        edit["_messages"] = ctx.get("messages", [])
        if routing_info:
            edit["_routing"] = routing_info


async def _store_pending_edits(
    session_key: str,
    tool_uses: list[dict],
    prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> None:
    """Convert tool_uses to edit records and stash them as pending.

    `prompt_context` carries model + system + messages from the triggering
    request body so they can be attached to the ingest payload at resolve
    time. Without this, the dashboard has no prompt or model to display.

    `routing_info` is the dynamic routing decision (if any) made for this
    request and is attached to each edit so it can be included in the ingest
    payload when the edit is resolved.
    """
    now = time.time()
    ctx = prompt_context or {}
    async with _pending_edits_lock:
        # Hard cap: if we're at the limit, drop the oldest entries.
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

    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            _ingest_edit(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


async def _cleanup_pending_edits_loop() -> None:
    """Background task: evict pending edits older than the TTL."""
    while True:
        try:
            await asyncio.sleep(300)
            now = time.time()
            async with _pending_edits_lock:
                expired = [
                    key for key, edits in _pending_edits.items()
                    if edits and (now - edits[0].get("_proposed_at", now)) > _PENDING_EDITS_TTL_SECONDS
                ]
                for key in expired:
                    _pending_edits.pop(key, None)
            if expired:
                logger.info("evicted %d expired pending edits", len(expired))
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("pending edits cleanup loop error")


# ── OpenAI Codex CLI adapter (Responses API function_call/function_call_output) ─

def _is_codex_responses_path(url: str) -> bool:
    """Return True iff the upstream URL targets OpenAI's Responses API."""
    try:
        return "/v1/responses" in urllib.parse.urlparse(url).path
    except Exception:
        return False


def _codex_session_key(body_dict: dict, headers: dict) -> str:
    """Stable session fingerprint for OpenAI Responses API.

    Uses the `instructions` field (the Responses API equivalent of system
    prompt) and falls back to any system/developer role message in input[].
    """
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
    """Parse Codex CLI's apply_patch DSL into per-file edit records.

    Supports the four verbs (Add File, Update File, Delete File, Move to)
    and the unified-diff-ish hunk format (@@, +, -, space-prefixed context).
    """
    if not isinstance(patch, str) or not patch.strip():
        return []

    # splitlines() handles LF / CRLF / CR uniformly and strips the line
    # terminator, so content lines won't carry a trailing \r on Windows-
    # generated patches.
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
                continue  # hunk header; we don't need the context for now
            if line.startswith("+"):
                current["new_lines"].append(line[1:])
            elif line.startswith("-"):
                current["old_lines"].append(line[1:])
            elif line.startswith(" "):
                # context line — present in both old and new
                current["old_lines"].append(line[1:])
                current["new_lines"].append(line[1:])
            # any other line is silently ignored

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
    """Convert one Codex function_call item into edit records.

    Only `apply_patch` produces records in v1. `shell` is logged-and-skipped
    (heredoc edits are detectable but the expert recommends not attributing
    them until a v2). All other tools (read-only) produce no records.
    """
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
    """Assemble function_call items from a Responses API SSE stream.

    Tracks items by output_index. Accumulates `response.function_call_arguments.delta`
    fragments. On `response.function_call_arguments.done` and `response.output_item.done`,
    prefers the authoritative full arguments string if provided, otherwise uses
    the accumulated delta fragments.
    """
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

    # Anything still without arguments — use accumulated deltas.
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

    # The Responses API stores all input items in `input[]`. Capture every
    # non-tool item so the dashboard can show the full conversation, not
    # just the latest user turn.
    inputs = body_dict.get("input")
    safe_messages: list[dict] = []
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            # Skip the tool plumbing items — they're not user-visible content.
            if item_type in ("function_call", "function_call_output"):
                continue
            safe_messages.append(item)

    return {
        "model": model,
        "system": instructions,
        "messages": safe_messages,
    }


def _classify_codex_function_call_output(item: dict) -> tuple[str, str]:
    """Map a Codex function_call_output to (status, error_message).

    Codex doesn't carry an is_error flag — use text heuristics on the output.
    """
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

    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            _ingest_edit(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


# ── Google Gemini CLI adapter (functionCall/functionResponse) ─────────────────

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
    """Stable session fingerprint for Gemini API.

    Reads from systemInstruction (Gemini's system prompt slot) and supports
    both the parts-list shape and a plain text fallback. Prefixed with
    'gemini|' so the hash cannot collide with Anthropic or Codex.
    """
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
    """Convert one Gemini functionCall into one or more edit records.

    Gemini's `args` is a JSON object (NOT a JSON string like OpenAI). The
    optional `id` field on the call is used as tool_use_id when present;
    otherwise a random uuid is assigned (correlation falls back to FIFO
    name matching at resolution time).
    """
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

    # Prefer explicit id (newer Gemini versions); otherwise random — resolution
    # will fall back to FIFO matching by tool name.
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
    """Extract functionResponses from the LAST response-bearing message.

    Walks contents[] in reverse, returns all functionResponses from the most
    recent message that contains any. Earlier responses were resolved on
    prior requests (and resolve is idempotent — re-lookup finds no pending,
    silently skips).
    """
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
        # Gemini puts functionResponses under role "user" (common) or "function" (newer).
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
    """Pull model + systemInstruction + contents out of a Gemini request.

    Model comes from the URL path (e.g. /v1/models/gemini-1.5-pro:generateContent)
    since the body doesn't include it. systemInstruction.parts becomes the
    system prompt. contents[] becomes the messages.
    """
    model = ""
    try:
        path = urllib.parse.urlparse(url).path or ""
        # Path looks like: /v1/models/<model>:generateContent or :streamGenerateContent
        m = re.search(r"/models/([^:/]+)", path)
        if m:
            model = m.group(1)
    except Exception:
        pass

    if not isinstance(body_dict, dict):
        return {"model": model, "system": "", "messages": []}

    # systemInstruction can be a dict with parts[] or a string
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

        # Pull a representative output string for keyword scanning.
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
    """Parse Gemini functionCalls into edit records and store as pending.

    Edits are stored in `_pending_edits` keyed by (session_key, synthetic_id).
    Python dict insertion order is preserved (3.7+), so resolution by FIFO
    iteration is deterministic.
    """
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
      1. If the functionResponse has an `id` field (newer Gemini versions),
         look up the pending entry by that exact synthetic_id.
      2. Otherwise, do FIFO name-matching: find the oldest pending entry in
         this session whose tool_name equals the response's name. Python
         dict insertion order makes this deterministic and matches Google's
         documented preservation of call/response order within a turn.
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
                # FIFO by name (relies on Python 3.7+ dict insertion order)
                for key in _pending_edits.keys():
                    if key[0] != session_key:
                        continue
                    edits = _pending_edits[key]
                    if edits and edits[0].get("tool_name") == response_name:
                        matched_key = key
                        break

            if matched_key is None:
                continue

            status, error_message = _classify_gemini_function_response(fr)
            for edit in _pending_edits.pop(matched_key):
                resolved.append((edit, status, error_message))

    for edit, status, error_message in resolved:
        task = asyncio.create_task(
            _ingest_edit(edit, session_key, status, error_message, provider)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


# ── ingest ────────────────────────────────────────────────────────────────────

async def _ingest_edit(
    edit: dict,
    session_key: str,
    status: str,
    error_message: str,
    provider: str,
) -> None:
    """Send a resolved structured edit to the backend."""
    if _backend_url_points_to_proxy():
        logger.error(
            "BACKEND_URL points at the proxy itself — skipping edit capture to avoid leaking payloads upstream."
        )
        return

    if not INGEST_TOKEN:
        logger.debug("BACKEND_INGEST_TOKEN not configured — skipping edit capture")
        return

    new_string = _redact(edit.get("new_string") or "")
    old_string = _redact(edit.get("old_string") or "")
    if not new_string and not old_string:
        return

    redacted_messages = _redact_value(edit.get("_messages", []) or [])
    redacted_system = _redact_value(edit.get("_system", "") or "")

    payload = {
        "id": str(uuid.uuid4()),
        "timestampIso": datetime.now(tz=UTC).isoformat(),
        "filePath": edit.get("file_path", "proxy-capture") or "proxy-capture",
        "insertedText": new_string,
        "workspaceId": WORKSPACE_ID,
        # Top-level promptMessages is what the backend's ingest normalizer reads
        # (see ingest_normalizer._resolve_prompt_messages). modelName lives only
        # inside provenance to avoid duplication.
        "promptMessages": redacted_messages,
        "systemPrompt": redacted_system,
        "provenance": _redact_value({
            "source": "lineagelens-universal-proxy",
            "provider": provider,
            "toolUseId": edit.get("tool_use_id", ""),
            "toolName": edit.get("tool_name", ""),
            "editIndex": edit.get("edit_index", 0),
            "oldString": old_string,
            "verb": edit.get("verb", ""),
            "movedTo": edit.get("moved_to", ""),
            "status": status,
            "errorMessage": error_message,
            "sessionKey": session_key,
            "modelName": edit.get("_model", "") or "",
        }),
    }
    # Attach routing decision if this request was model-routed.
    routing_info = edit.get("_routing")
    if routing_info:
        payload["routing"] = routing_info

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/ingest",
                json=payload,
                headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
            )
            resp.raise_for_status()
        logger.info(
            "edit captured: tool=%s file=%s status=%s",
            edit.get("tool_name"), edit.get("file_path"), status,
        )
    except Exception as exc:
        logger.error(
            "Failed to deliver edit to backend: %s %s",
            type(exc).__name__, exc,
        )


async def _ingest(
    text: str,
    upstream_path: str,
    provider: str = "unknown",
    file_path: str = "proxy-capture",
    upstream_method: str = "POST",
    upstream_status: int = 200,
    routing_info: dict | None = None,
) -> None:
    if not text.strip():
        return
    if _backend_url_points_to_proxy():
        logger.error(
            "BACKEND_URL points at the proxy itself — skipping capture to avoid leaking payloads upstream."
        )
        return
    if not INGEST_TOKEN:
        logger.debug("BACKEND_INGEST_TOKEN not configured — skipping capture")
        return

    code = _redact(_extract_code(text))
    if not code:
        return

    payload: dict = {
        "id": str(uuid.uuid4()),
        "timestampIso": datetime.now(tz=UTC).isoformat(),
        "filePath": file_path,
        "insertedText": code,
        "workspaceId": WORKSPACE_ID,
        "provenance": {
            "source": "lineagelens-universal-proxy",
            "upstreamPath": upstream_path,
            "upstreamMethod": upstream_method,
            "upstreamStatus": upstream_status,
            "provider": provider,
        },
    }
    if routing_info:
        payload["routing"] = routing_info

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/ingest",
                json=payload,
                headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
            )
            resp.raise_for_status()
        logger.info("captured %d chars → backend (provider=%s)", len(code), provider)
    except Exception as exc:
        logger.error(
            "Failed to deliver ingest to backend: %s %s", type(exc).__name__, exc
        )
        # Do NOT re-raise — the LLM response was already sent to the client


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/proxy-health")
async def proxy_health() -> dict:
    return {"status": "ok"}


async def _handle_streaming(
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    safe_path: str,
    provider: str = "unknown",
    file_path: str = "proxy-capture",
    session_key: str = "",
    codex_session_key: str = "",
    is_codex: bool = False,
    gemini_session_key: str = "",
    anthropic_prompt_context: dict | None = None,
    codex_prompt_context: dict | None = None,
    gemini_prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> Response:
    # URL base is from UPSTREAM_URL (env-configured, trusted); only path is from request.
    # Scheme and host are pinned by _build_upstream_url — never sourced from user input.
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0))
    try:
        upstream = await client.send(
            client.build_request(method, url, headers=headers, content=body),
            stream=True,
        )
    except Exception as exc:
        await client.aclose()
        logger.exception("upstream error: %s", exc)
        return Response(content="Bad gateway", status_code=502)

    # Pre-flight Content-Length check: if upstream declares a body that is too
    # large we still forward it transparently but skip provenance capture.
    skip_capture = False
    resp_content_length = upstream.headers.get("content-length")
    if resp_content_length:
        try:
            if int(resp_content_length) > MAX_RESPONSE_BODY_BYTES:
                skip_capture = True
                logger.warning(
                    "Response body too large to capture (%d bytes); proxying without storage",
                    int(resp_content_length),
                )
        except ValueError:
            pass

    collected: list[bytes] = []
    _collected_bytes = 0
    _capture_overflow = False

    async def stream_gen():
        nonlocal _collected_bytes, _capture_overflow
        try:
            async for chunk in upstream.aiter_bytes():
                if not skip_capture and not _capture_overflow:
                    _collected_bytes += len(chunk)
                    if _collected_bytes > MAX_BODY_BYTES:
                        _capture_overflow = True
                    else:
                        collected.append(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            if skip_capture:
                pass  # already logged above
            elif upstream.status_code >= 400:
                logger.debug("skipping capture: upstream status %d", upstream.status_code)
            elif not _capture_overflow:
                # Try structured capture first: Anthropic tool_use, Codex
                # function_call, or Gemini functionCall.
                structured_captured = False
                if provider == "anthropic" and session_key:
                    tool_uses = _extract_anthropic_tool_uses_from_sse(collected)
                    if tool_uses:
                        await _store_pending_edits(session_key, tool_uses, anthropic_prompt_context, routing_info)
                        structured_captured = True
                elif is_codex and codex_session_key:
                    function_calls = _extract_codex_function_calls_from_sse(collected)
                    if function_calls:
                        await _store_codex_pending_edits(codex_session_key, function_calls, codex_prompt_context, routing_info)
                        structured_captured = True
                elif provider == "gemini" and gemini_session_key:
                    function_calls = _extract_gemini_function_calls_from_sse(collected)
                    if function_calls:
                        await _store_gemini_pending_edits(gemini_session_key, function_calls, gemini_prompt_context, routing_info)
                        structured_captured = True

                if not structured_captured:
                    text = _text_from_sse(collected, provider)
                    _task = asyncio.create_task(
                        _ingest(
                            text, f"/{safe_path}", provider=provider, file_path=file_path,
                            upstream_method=method, upstream_status=upstream.status_code,
                            routing_info=routing_info,
                        )
                    )
                    _background_tasks.add(_task)
                    _task.add_done_callback(_background_tasks.discard)
            else:
                logger.warning("streaming response too large, skipping capture")

    return StreamingResponse(
        stream_gen(),
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


async def _handle_non_streaming(
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    safe_path: str,
    provider: str = "unknown",
    file_path: str = "proxy-capture",
    session_key: str = "",
    codex_session_key: str = "",
    is_codex: bool = False,
    gemini_session_key: str = "",
    anthropic_prompt_context: dict | None = None,
    codex_prompt_context: dict | None = None,
    gemini_prompt_context: dict | None = None,
    routing_info: dict | None = None,
) -> Response:
    # URL base is from UPSTREAM_URL (env-configured, trusted); only path is from request.
    # Scheme and host are pinned by _build_upstream_url — never sourced from user input.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=120.0)) as client:
            upstream = await client.request(method, url, headers=headers, content=body)
    except Exception as exc:
        logger.exception("upstream error: %s", exc)
        return Response(content="Bad gateway", status_code=502)

    # Pre-flight Content-Length check on the upstream response.
    resp_content_length = upstream.headers.get("content-length")
    skip_capture = False
    if resp_content_length:
        try:
            if int(resp_content_length) > MAX_RESPONSE_BODY_BYTES:
                skip_capture = True
                logger.warning(
                    "Response body too large to capture (%d bytes); proxying without storage",
                    int(resp_content_length),
                )
        except ValueError:
            pass

    if not skip_capture and upstream.status_code < 400:
        if len(upstream.content) <= MAX_BODY_BYTES:
            # Wire up savings estimate now that token counts are available.
            if routing_info and routing_info.get("savings_estimate_usd") == 0.0:
                try:
                    resp_json = upstream.json()
                    usage = resp_json.get("usage") or {}
                    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
                    if input_tokens or output_tokens:
                        routing_info["savings_estimate_usd"] = estimate_savings(
                            routing_info["originalModel"],
                            routing_info["routedModel"],
                            input_tokens,
                            output_tokens,
                        )
                except Exception:
                    pass

            # Prefer structured edit capture over text scraping when available:
            # Anthropic tool_use, Codex Responses API function_call, or Gemini
            # functionCall.
            structured_captured = False
            if provider == "anthropic" and session_key:
                tool_uses = _extract_anthropic_tool_uses_from_body(upstream.content)
                if tool_uses:
                    await _store_pending_edits(session_key, tool_uses, anthropic_prompt_context, routing_info)
                    structured_captured = True
            elif is_codex and codex_session_key:
                function_calls = _extract_codex_function_calls_from_body(upstream.content)
                if function_calls:
                    await _store_codex_pending_edits(codex_session_key, function_calls, codex_prompt_context, routing_info)
                    structured_captured = True
            elif provider == "gemini" and gemini_session_key:
                function_calls = _extract_gemini_function_calls_from_body(upstream.content)
                if function_calls:
                    await _store_gemini_pending_edits(gemini_session_key, function_calls, gemini_prompt_context, routing_info)
                    structured_captured = True

            if not structured_captured:
                text = _text_from_body(upstream.content, provider=provider)
                if text:
                    _task = asyncio.create_task(
                        _ingest(
                            text, f"/{safe_path}", provider=provider, file_path=file_path,
                            upstream_method=method, upstream_status=upstream.status_code,
                            routing_info=routing_info,
                        )
                    )
                    _background_tasks.add(_task)
                    _task.add_done_callback(_background_tasks.discard)
        else:
            logger.warning(
                "upstream response too large (%d bytes), skipping capture",
                len(upstream.content),
            )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


async def _read_request_body(request: Request) -> Response | bytes:
    """Read and size-limit the request body.

    Returns a Response (4xx) if the body is invalid or too large, otherwise
    returns the raw bytes. Extracted to keep proxy_request complexity in check.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                return Response(content="Request body too large", status_code=413)
        except ValueError:
            return Response(content="Invalid Content-Length header", status_code=400)

    body_parts: list[bytes] = []
    seen_bytes = 0
    async for chunk in request.stream():
        seen_bytes += len(chunk)
        if seen_bytes > MAX_BODY_BYTES:
            return Response(content="Request body too large", status_code=413)
        body_parts.append(chunk)
    return b"".join(body_parts)


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_request(request: Request, path: str) -> Response:
    safe_path = _sanitize_path(path)

    body_or_error = await _read_request_body(request)
    if isinstance(body_or_error, Response):
        return body_or_error
    body: bytes = body_or_error

    headers = _fwd_headers(request.headers)

    # ── Provider detection (inbound-first) ────────────────────────────────────
    # Step 1: detect from the raw inbound path + headers — BEFORE the URL is
    # rewritten to an upstream base.  This lets a single proxy serve Claude Code
    # (Anthropic), Codex CLI (OpenAI), and Gemini CLI simultaneously when each
    # tool sends to /v1/messages, /v1/chat/completions, or /v1beta/models/…
    provider = detect_provider_from_inbound("/" + safe_path, dict(request.headers))

    # Step 2: build the upstream URL with the provider-specific base URL.
    # If provider is unknown here, fall back to the generic UPSTREAM_URL — the
    # URL-based detect_provider_and_format call below will refine the provider.
    url = _build_upstream_url_for_provider(provider, safe_path, request.url.query)

    # Step 3: if inbound detection was inconclusive, fall back to URL-based
    # detection (original behaviour — examines the upstream domain/path).
    if provider == "unknown":
        provider = detect_provider_and_format(url, dict(request.headers))

    logger.debug("provider routing: inbound=/%s → provider=%s upstream=%s", safe_path, provider, url)

    # Parse request body JSON once and reuse across all provider adapters (R3).
    try:
        req_body_dict: dict | None = json.loads(body) if body else None
        if not isinstance(req_body_dict, dict):
            req_body_dict = None
    except Exception:
        req_body_dict = None

    # Anthropic adapter: resolve pending edits from any tool_result blocks the
    # client is sending back from a previous turn, and extract the prompt
    # context for the new turn (model + messages) so it can be attached to
    # the next response's pending edits.
    anthropic_session_key = ""
    anthropic_prompt_context: dict = {}
    if provider == "anthropic" and body:
        try:
            if isinstance(req_body_dict, dict):
                anthropic_session_key = _session_key(req_body_dict, dict(request.headers))
                anthropic_prompt_context = _extract_anthropic_prompt_context(req_body_dict)
                tool_results = _extract_anthropic_tool_results(body)
                if tool_results:
                    await _resolve_pending_edits(anthropic_session_key, tool_results, provider)
        except Exception:
            logger.debug("anthropic adapter: request body parse failed", exc_info=True)

    # Codex CLI adapter: same pattern but for OpenAI Responses API.
    is_codex = provider == "openai" and _is_codex_responses_path(url)
    codex_session_key = ""
    codex_prompt_context: dict = {}
    if is_codex and body:
        try:
            if isinstance(req_body_dict, dict):
                codex_session_key = _codex_session_key(req_body_dict, dict(request.headers))
                codex_prompt_context = _extract_codex_prompt_context(req_body_dict)
                fc_outputs = _extract_codex_function_call_outputs(body)
                if fc_outputs:
                    await _resolve_codex_pending_edits(codex_session_key, fc_outputs, provider)
        except Exception:
            logger.debug("codex adapter: request body parse failed", exc_info=True)

    # Gemini CLI adapter: Google Gemini API functionCall / functionResponse.
    gemini_session_key = ""
    gemini_prompt_context: dict = {}
    if provider == "gemini" and body:
        try:
            if isinstance(req_body_dict, dict):
                gemini_session_key = _gemini_session_key(req_body_dict, dict(request.headers))
                gemini_prompt_context = _extract_gemini_prompt_context(req_body_dict, url)
                fn_responses = _extract_gemini_function_responses(body)
                if fn_responses:
                    await _resolve_gemini_pending_edits(gemini_session_key, fn_responses, provider)
        except Exception:
            logger.debug("gemini adapter: request body parse failed", exc_info=True)

    # Extract the best available file path from request metadata.
    file_path = extract_file_path(dict(request.headers), req_body_dict)

    # ── Dynamic model routing ─────────────────────────────────────────────────
    # Classify the request and, if an enabled routing policy exists for this
    # workspace + provider, overwrite the model name before forwarding.
    # On any error the request is forwarded unchanged — never block the client.
    routing_info: dict | None = None
    try:
        if req_body_dict and provider in ("anthropic", "openai", "gemini"):
            tier = classify_request(req_body_dict)
            policy = await get_policy(WORKSPACE_ID, provider)
            if policy:
                target_model = policy.get("mappings", {}).get(tier)
                current_model = req_body_dict.get("model", "")
                if target_model and target_model != current_model and current_model:
                    req_body_dict["model"] = target_model
                    body = json.dumps(req_body_dict).encode()
                    routing_info = {
                        "originalModel": current_model,
                        "routedModel": target_model,
                        "tier": tier,
                        "policyId": str(policy.get("id", "")),
                        # savings_estimate_usd is added after upstream responds
                        # and token counts are known; for text-based captures
                        # token counts aren't available, so we default to 0.
                        "savings_estimate_usd": 0.0,
                    }
                    logger.info(
                        "routing: %s → %s (tier=%s workspace=%s)",
                        current_model, target_model, tier, WORKSPACE_ID,
                    )
    except Exception as _routing_err:
        logger.warning("routing error (request forwarded unchanged): %s", _routing_err)
    # ── End routing ───────────────────────────────────────────────────────────

    if _is_streaming(url, body):
        return await _handle_streaming(
            request.method, url, headers, body, safe_path,
            provider=provider, file_path=file_path,
            session_key=anthropic_session_key,
            codex_session_key=codex_session_key,
            is_codex=is_codex,
            gemini_session_key=gemini_session_key,
            anthropic_prompt_context=anthropic_prompt_context,
            codex_prompt_context=codex_prompt_context,
            gemini_prompt_context=gemini_prompt_context,
            routing_info=routing_info,
        )
    return await _handle_non_streaming(
        request.method, url, headers, body, safe_path,
        provider=provider, file_path=file_path,
        session_key=anthropic_session_key,
        codex_session_key=codex_session_key,
        is_codex=is_codex,
        gemini_session_key=gemini_session_key,
        anthropic_prompt_context=anthropic_prompt_context,
        codex_prompt_context=codex_prompt_context,
        gemini_prompt_context=gemini_prompt_context,
        routing_info=routing_info,
    )


def _split_http_target(target: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
        query = parsed.query
    else:
        path, _, query = target.partition("?")
        if not path:
            path = "/"
    return path.lstrip("/"), query


async def _read_chunked_http_body(reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    seen_bytes = 0

    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if line in (b"", b"\n", b"\r\n"):
            raise ValueError("Unexpected end of chunked request body.")

        size_text = line.strip().split(b";", 1)[0]
        try:
            chunk_size = int(size_text, 16)
        except ValueError as exc:
            raise ValueError("Invalid chunked request body.") from exc

        if chunk_size == 0:
            while True:
                trailer = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if trailer in (b"\r\n", b"\n", b""):
                    break
            break

        chunk = await asyncio.wait_for(reader.readexactly(chunk_size), timeout=30.0)
        terminator = await asyncio.wait_for(reader.readexactly(2), timeout=5.0)
        if terminator != b"\r\n":
            raise ValueError("Invalid chunked request body.")

        seen_bytes += len(chunk)
        if seen_bytes > MAX_BODY_BYTES:
            raise ValueError("Request body too large")
        chunks.append(chunk)

    return b"".join(chunks)


async def _read_http_request_body(reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding:
        return await _read_chunked_http_body(reader)

    content_length = headers.get("content-length", "").strip()
    if not content_length:
        return b""

    try:
        body_length = int(content_length)
    except ValueError as exc:
        raise ValueError("Invalid Content-Length header.") from exc

    if body_length < 0 or body_length > MAX_BODY_BYTES:
        raise ValueError("Request body too large")

    if body_length == 0:
        return b""

    return await asyncio.wait_for(reader.readexactly(body_length), timeout=30.0)


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, str, list[tuple[str, str]], dict[str, str], bytes] | None:
    request_line = await asyncio.wait_for(reader.readline(), timeout=15.0)
    if request_line in (b"", b"\r\n", b"\n"):
        return None

    parts = request_line.rstrip(b"\r\n").split(b" ", 2)
    if len(parts) < 2:
        raise ValueError("Invalid HTTP request line.")

    method = parts[0].decode("ascii", errors="replace").upper()
    target = parts[1].decode("utf-8", errors="replace")
    version = parts[2].decode("ascii", errors="replace") if len(parts) > 2 else "HTTP/1.1"

    header_items: list[tuple[str, str]] = []
    header_map: dict[str, str] = {}
    for _ in range(100):
        line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if line in (b"\r\n", b"\n", b""):
            break
        if b":" not in line:
            raise ValueError("Invalid HTTP header line.")

        name_bytes, value_bytes = line.split(b":", 1)
        name = name_bytes.decode("latin-1").strip()
        value = value_bytes.decode("latin-1").strip()
        header_items.append((name, value))
        header_map[name.lower()] = value

    body = await _read_http_request_body(reader, header_map)
    return method, target, version, header_items, header_map, body


async def _write_plain_http_error(
    writer: asyncio.StreamWriter,
    status_code: int,
    message: str,
) -> None:
    try:
        reason = HTTPStatus(status_code).phrase
    except ValueError:
        reason = "Error"

    body = message.encode("utf-8")
    writer.write(f"HTTP/1.1 {status_code} {reason}\r\n".encode("latin-1"))
    writer.write(b"content-type: text/plain; charset=utf-8\r\n")
    writer.write(f"content-length: {len(body)}\r\n".encode("ascii"))
    writer.write(b"connection: close\r\n\r\n")
    writer.write(body)
    await writer.drain()


async def _write_http_response(
    writer: asyncio.StreamWriter,
    response: Response,
    method: str,
) -> None:
    try:
        reason = HTTPStatus(response.status_code).phrase
    except ValueError:
        reason = "OK"

    is_streaming = isinstance(response, StreamingResponse)
    headers = list(getattr(response, "raw_headers", []))
    if is_streaming:
        headers = [
            (name, value)
            for name, value in headers
            if name.lower() not in {b"content-length", b"transfer-encoding"}
        ]
        headers.append((b"transfer-encoding", b"chunked"))
    else:
        body = getattr(response, "body", b"") or b""
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not any(name.lower() == b"content-length" for name, _ in headers):
            headers.append((b"content-length", str(len(body)).encode("ascii")))

    writer.write(f"HTTP/1.1 {response.status_code} {reason}\r\n".encode("latin-1"))
    for name, value in headers:
        writer.write(name + b": " + value + b"\r\n")
    writer.write(b"\r\n")

    if method.upper() == "HEAD":
        await writer.drain()
        return

    if is_streaming:
        async for chunk in response.body_iterator:
            if not chunk:
                continue
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            writer.write(f"{len(chunk):X}\r\n".encode("ascii"))
            writer.write(chunk)
            writer.write(b"\r\n")
            await writer.drain()

        writer.write(b"0\r\n\r\n")
        await writer.drain()
        return

    body = getattr(response, "body", b"") or b""
    if isinstance(body, str):
        body = body.encode("utf-8")
    writer.write(body)
    await writer.drain()


async def _handle_mitm_http_requests(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    host: str,
    port: int,
) -> None:
    while True:
        try:
            parsed_request = await _read_http_request(client_reader)
        except asyncio.TimeoutError:
            return
        except ValueError as exc:
            logger.warning("MITM HTTP parse error for %s:%s — %s", host, port, exc)
            await _write_plain_http_error(client_writer, 400, str(exc))
            return

        if parsed_request is None:
            return

        method, target, version, header_items, header_map, body = parsed_request
        safe_path, query = _split_http_target(target)
        route_path = safe_path
        scope_path = f"/{route_path}" if route_path else "/"

        peer_name = client_writer.get_extra_info("peername")
        client = None
        if isinstance(peer_name, tuple) and len(peer_name) >= 2:
            client = (str(peer_name[0]), int(peer_name[1]))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": version.split("/", 1)[-1] if "/" in version else version,
            "method": method,
            "scheme": "https",
            "path": scope_path,
            "raw_path": scope_path.encode("utf-8"),
            "query_string": query.encode("utf-8"),
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in header_items
            ],
            "client": client,
            "server": (host, port),
        }

        sent_request = False

        async def receive():
            nonlocal sent_request
            if sent_request:
                return {"type": "http.disconnect"}
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)

        try:
            response = await proxy_request(request, route_path)
        except Exception as exc:
            logger.exception("MITM proxy request failed for %s %s: %s", method, target, exc)
            await _write_plain_http_error(client_writer, 502, "Bad Gateway")
            return

        try:
            await _write_http_response(client_writer, response, method)
        except Exception as exc:
            logger.debug("MITM response relay failed for %s %s: %s", method, target, exc)
            return


# ── HTTPS CONNECT tunnel server ───────────────────────────────────────────────

def _generate_host_cert(hostname: str) -> tuple[bytes, bytes]:
    """Return a (cert_pem, key_pem) pair for hostname, signed by the proxy CA.

    Requires: pip install cryptography
    Results are cached in memory for the lifetime of the process.
    """
    if hostname in _host_cert_cache:
        return _host_cert_cache[hostname]

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime as _dt

    with open(PROXY_CA_CERT_PATH, "rb") as fh:
        ca_cert = x509.load_pem_x509_certificate(fh.read())
    with open(PROXY_CA_KEY_PATH, "rb") as fh:
        ca_key = serialization.load_pem_private_key(fh.read(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + _dt.timedelta(days=397))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    if len(_host_cert_cache) >= _HOST_CERT_CACHE_MAX:
        # Evict oldest entry (dict is insertion-ordered in Python 3.7+)
        _host_cert_cache.pop(next(iter(_host_cert_cache)))
    _host_cert_cache[hostname] = (cert_pem, key_pem)
    return cert_pem, key_pem


async def _pipe(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Copy bytes from reader to writer until EOF."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    # ConnectionResetError and BrokenPipeError are both subclasses of OSError;
    # keeping only the base class avoids the redundant-exception-class warning.
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _parse_connect_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> tuple[str, int] | None:
    """Read and parse the CONNECT request line and drain headers.

    Returns (host, port) on success, or None after writing a 400 response.
    """
    line = await asyncio.wait_for(reader.readline(), timeout=15.0)
    parts = line.rstrip(b"\r\n").split(b" ")
    if len(parts) < 2 or parts[0].upper() != b"CONNECT":
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return None

    host_port = parts[1].decode("ascii", errors="replace")
    host, _, port_str = host_port.rpartition(":")
    port = int(port_str) if port_str.isdigit() else 443

    # Drain remaining request headers; cap at 100 lines to prevent slow-header DoS.
    for _ in range(100):
        hline = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if hline in (b"\r\n", b"\n", b""):
            break
    else:
        writer.write(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
        await writer.drain()
        return None

    return host, port


async def _connect_to_upstream(
    host: str,
    port: int,
    writer: asyncio.StreamWriter,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Open a TCP (or TLS) connection to the upstream host.

    Returns (up_reader, up_writer) on success, or None after writing a 502.
    When MITM is enabled the connection uses TLS so we can intercept traffic.
    """
    import ssl as _ssl

    try:
        if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
            # MITM: connect to real server with TLS so we can forward decrypted traffic.
            # ssl.create_default_context() is already secure (TLS 1.2+ CA-verified).
            server_ctx = _ssl.create_default_context()
            # server_hostname is required for hostname verification against the
            # upstream cert's SANs. Without it, a network attacker who can
            # redirect DNS or routes can present a valid cert for a different
            # domain and we'd forward the decrypted stream to them.
            up_reader, up_writer = await asyncio.open_connection(
                host, port, ssl=server_ctx, server_hostname=host
            )
        else:
            # Plain TCP tunnel: client-side TLS is established within the tunnel
            # by the connecting application (browser/SDK). The proxy must not add
            # its own TLS layer here, as that would break the end-to-end handshake.
            up_reader, up_writer = await asyncio.open_connection(host, port)
    except Exception as exc:
        logger.warning("CONNECT upstream error %s:%s — %s", host, port, exc)
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        return None

    return up_reader, up_writer


async def _write_temp_pem(data: bytes, suffix: str = ".pem") -> str:
    """Write *data* to a new temporary file and return its path.

    Uses anyio for non-blocking I/O. The caller is responsible for unlinking
    the file when it is no longer needed.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        os.close(fd)
    except OSError as exc:
        logger.warning("Failed to close mkstemp fd for %s: %s", tmp_path, exc)
        raise
    async with await anyio.open_file(tmp_path, "wb") as fh:
        await fh.write(data)
    return tmp_path


async def _mitm_upgrade_client_tls(
    host: str,
    client_writer: asyncio.StreamWriter,
) -> bool:
    """Upgrade the inbound client connection to TLS for MITM interception.

    Generates a per-host certificate signed by the proxy CA, writes it to
    temporary files, and performs a server-side TLS handshake with the client.
    Temporary files are always cleaned up.  Returns True on success.
    """
    import ssl as _ssl

    cert_pem, key_pem = _generate_host_cert(host)
    cert_file = key_file = ""
    try:
        cert_file = await _write_temp_pem(cert_pem, suffix=".pem")
        key_file = await _write_temp_pem(key_pem, suffix=".pem")

        # PROTOCOL_TLS_SERVER with TLSv1_2 minimum ensures the proxy never
        # negotiates a protocol weaker than TLS 1.2 with the connecting client.
        client_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        client_ctx.minimum_version = _ssl.TLSVersion.TLSv1_2
        client_ctx.load_cert_chain(cert_file, key_file)

        loop = asyncio.get_running_loop()
        transport = client_writer.transport
        protocol = transport.get_protocol()
        await loop.start_tls(transport, protocol, client_ctx, server_side=True)
        # After start_tls the StreamReader/Writer are updated in-place.
        return True
    except Exception as exc:
        logger.warning(
            "MITM TLS upgrade failed for %s: %s", host, exc
        )
        return False
    finally:
        for f in (cert_file, key_file):
            if f:
                try:
                    os.unlink(f)
                except OSError:
                    pass


async def _handle_connect_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one inbound CONNECT request."""
    try:
        result = await _parse_connect_request(client_reader, client_writer)
        if result is None:
            return
        host, port = result

        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
            ok = await _mitm_upgrade_client_tls(host, client_writer)
            if not ok:
                return

            await _handle_mitm_http_requests(client_reader, client_writer, host, port)
            return

        up_pair = await _connect_to_upstream(host, port, client_writer)
        if up_pair is None:
            return
        up_reader, up_writer = up_pair

        await asyncio.gather(
            _pipe(client_reader, up_writer),
            _pipe(up_reader, client_writer),
            return_exceptions=True,
        )

    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as exc:
        logger.debug("CONNECT handler error: %s", exc)
    finally:
        try:
            client_writer.close()
        except Exception:
            pass


async def _run_connect_server() -> None:
    """Run the HTTPS CONNECT tunnel server on PROXY_CONNECT_PORT."""
    server = await asyncio.start_server(
        _handle_connect_client, PROXY_HOST, PROXY_CONNECT_PORT
    )
    mitm_status = "MITM enabled" if (PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH) else "transparent relay"
    logger.info(
        "CONNECT proxy listening on %s:%d (%s)",
        PROXY_HOST, PROXY_CONNECT_PORT, mitm_status,
    )
    async with server:
        await server.serve_forever()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    async def _main() -> None:
        config = uvicorn.Config(app, host=PROXY_HOST, port=PROXY_PORT, log_level="info")
        server = uvicorn.Server(config)
        logger.info("LineageLens Universal Proxy starting on port %d", PROXY_PORT)
        logger.info("Upstream : %s", UPSTREAM_URL)
        logger.info("Backend  : %s", BACKEND_URL)
        logger.info("Workspace: %s", WORKSPACE_ID)
        logger.info("Token    : %s", "configured" if INGEST_TOKEN else "NOT SET — captures will be skipped")
        logger.info("CONNECT  : port %d", PROXY_CONNECT_PORT)

        # Guard: BACKEND_URL must not point at the proxy itself.
        if _backend_url_points_to_proxy():
            logger.error(
                "MISCONFIGURATION: BACKEND_URL (%s) points at the proxy itself — ingest calls would loop. "
                "Set BACKEND_URL to the LineageLens backend address.",
                BACKEND_URL,
            )
            raise SystemExit(1)

        if not INGEST_TOKEN:
            logger.warning("BACKEND_INGEST_TOKEN is not set — all AI captures will be skipped.")

        # Validate CA cert/key files exist before the first CONNECT arrives.
        if PROXY_CA_CERT_PATH or PROXY_CA_KEY_PATH:
            missing = [p for p in (PROXY_CA_CERT_PATH, PROXY_CA_KEY_PATH) if not os.path.exists(p)]
            if missing:
                logger.error(
                    "MITM mode requested but CA file(s) missing: %s — CONNECT interception will fail. "
                    "Provide valid PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH or unset both.",
                    ", ".join(missing),
                )

        await asyncio.gather(server.serve(), _run_connect_server())

    asyncio.run(_main())
