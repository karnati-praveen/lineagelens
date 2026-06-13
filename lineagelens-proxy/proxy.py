#!/usr/bin/env python3
"""
LineageLens Universal LLM Proxy

Transparently forwards requests to any LLM API (Anthropic, OpenAI, or any
compatible endpoint) and captures AI-generated code into the LineageLens backend.

Works with: Claude Code, Codex CLI, Gemini CLI, Goose, Continue, any CLI or IDE
that supports a configurable base URL.

Setup:
    export ANTHROPIC_BASE_URL=http://localhost:8788   # Claude Code / Anthropic SDK
    export OPENAI_BASE_URL=http://localhost:8788       # OpenAI SDK / compatible tools
"""
import asyncio
import json
import logging
import os
import posixpath
import re
import urllib.parse

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from classifier import classify_request
from pricing import estimate_savings
from routing_cache import cancel_refresh_loop, get_policy, init_routing_cache

# ── Sub-module imports (also re-exported as proxy.* attributes for tests) ──────

# These four URL vars are defined here (not in config.py) so that
# importlib.reload(proxy) re-reads them from the environment — used by tests.
UPSTREAM_URL            = os.environ.get("UPSTREAM_URL", "https://api.anthropic.com").rstrip("/")
_ANTHROPIC_UPSTREAM_URL = os.environ.get("ANTHROPIC_UPSTREAM_URL", "").rstrip("/")
_OPENAI_UPSTREAM_URL    = os.environ.get("OPENAI_UPSTREAM_URL",    "").rstrip("/")
_GEMINI_UPSTREAM_URL    = os.environ.get("GEMINI_UPSTREAM_URL",    "").rstrip("/")

from config import (
    BACKEND_URL,
    INGEST_TOKEN,
    WORKSPACE_ID,
    PROXY_PORT,
    PROXY_HOST,
    MAX_BODY_BYTES,
    REDACT_PATTERNS,
    PROXY_CONNECT_PORT,
    PROXY_CA_CERT_PATH,
    PROXY_CA_KEY_PATH,
    PROXY_CONNECT_TOKEN,
    _BLOCKED_CONNECT_HOSTS,
    _KNOWN_LLM_DOMAINS,
    MAX_RESPONSE_BODY_BYTES,
    _backend_url_points_to_proxy,
    _DEFAULT_REDACT_PATTERN_STRINGS,
)

from ingest import _ingest, _ingest_edit, _ingest_agent_actions, _redact, _redact_value

from adapters.common import (
    _pending_edits,
    _pending_edits_lock,
    _PENDING_EDITS_TTL_SECONDS,
    _PENDING_EDITS_MAX,
    _SSE_DATA_PREFIX,
    _SSE_DONE_MARKER,
    _background_tasks,
    _cleanup_pending_edits_loop,
    _annotate_edits,
    _delta_from_sse_payload,
    _text_from_body,
    _text_from_sse,
    _text_from_chunk,
)

from adapters.anthropic import (
    _FILE_MUTATING_TOOLS,
    _session_key,
    _parse_anthropic_tool_use_to_edits,
    _extract_anthropic_tool_uses_from_body,
    _extract_anthropic_tool_uses_from_sse,
    _extract_anthropic_tool_results,
    _extract_anthropic_prompt_context,
    _classify_tool_result,
    _store_pending_edits,
    _resolve_pending_edits,
    _extract_anthropic_agent_actions,
    _classify_agent_action_type,
    _compute_prompt_context_id,
)

from adapters.codex import (
    _is_codex_responses_path,
    _codex_session_key,
    _parse_apply_patch_dsl,
    _parse_codex_function_call_to_edits,
    _extract_codex_function_calls_from_body,
    _extract_codex_function_calls_from_sse,
    _extract_codex_function_call_outputs,
    _classify_codex_function_call_output,
    _extract_codex_prompt_context,
    _store_codex_pending_edits,
    _resolve_codex_pending_edits,
)

from adapters.gemini import (
    _GEMINI_FILE_MUTATING_TOOLS,
    _gemini_session_key,
    _parse_gemini_function_call_to_edits,
    _extract_gemini_function_calls_from_body,
    _extract_gemini_function_calls_from_sse,
    _extract_gemini_function_responses,
    _extract_gemini_prompt_context,
    _classify_gemini_function_response,
    _store_gemini_pending_edits,
    _resolve_gemini_pending_edits,
)

from connect_tunnel import _assert_not_open_relay, _run_connect_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lineagelens-proxy")

_DROP_REQ  = {"host", "content-length", "transfer-encoding", "connection",
              "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
_DROP_RESP = {"content-encoding", "transfer-encoding", "connection", "content-length"}


# ── Provider detection ────────────────────────────────────────────────────────

def detect_provider_and_format(url: str, headers: dict) -> str:
    """Detect which LLM provider this request targets.

    Checks the destination URL, headers, and request path.
    Returns one of: "anthropic", "openai", "gemini", or "unknown".
    """
    url_lower = url.lower()
    header_keys = {k.lower() for k in headers}

    if "anthropic.com" in url_lower or "anthropic-version" in header_keys:
        return "anthropic"
    if "googleapis.com" in url_lower or "generativelanguage.googleapis.com" in url_lower:
        return "gemini"
    if "openai.com" in url_lower:
        return "openai"
    if "openai.azure.com" in url_lower:
        return "openai"
    for host in ("api.together.xyz", "api.groq.com", "api.fireworks.ai", "api.mistral.ai"):
        if host in url_lower:
            return "openai"
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

    if (
        "anthropic-version" in header_keys_lower
        or header_keys_lower.get("x-api-key-provider", "").lower() == "anthropic"
    ):
        return "anthropic"

    if (
        "/v1beta/" in path_lower
        or ":generatecontent" in path_lower
        or ":streamgeneratecontent" in path_lower
        or "gemini" in path_lower
    ):
        return "gemini"

    if (
        "/v1/chat/completions" in path_lower
        or "/v1/responses" in path_lower
        or "/v1/embeddings" in path_lower
        or "/v1/completions" in path_lower
    ):
        return "openai"

    if "/v1/messages" in path_lower:
        return "anthropic"

    auth = header_keys_lower.get("authorization", "")
    if auth.lower().startswith("bearer ya29.") or "x-goog" in header_keys_lower:
        return "gemini"

    return "unknown"


def _get_provider_upstream_base(provider: str) -> str:
    """Return the upstream base URL for a detected provider.

    Checks per-provider env vars first; falls back to the generic UPSTREAM_URL
    so existing single-upstream deployments continue to work unchanged.

    NOTE: reads module-level globals so test patches (p._ANTHROPIC_UPSTREAM_URL = "")
    propagate correctly without re-import.
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
    """Build the upstream URL from the fixed UPSTREAM_URL base and the sanitized path."""
    parsed = urllib.parse.urlparse(UPSTREAM_URL)
    upstream_path = parsed.path.rstrip("/") + "/" + safe_path if safe_path else parsed.path
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, upstream_path, "", raw_query, ""))


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


# ── FastAPI lifespan / app ─────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup/shutdown context for FastAPI (replaces deprecated on_event)."""
    task = asyncio.create_task(_cleanup_pending_edits_loop())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    try:
        await init_routing_cache()
    except Exception as _rc_err:
        logger.warning("routing cache init failed (routing disabled until next refresh): %s", _rc_err)
    # Single shared client reuses TCP+TLS connections across all proxy requests,
    # eliminating the ~100-300ms handshake overhead on every LLM call.
    app.state.proxy_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, read=300.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    )
    try:
        yield
    finally:
        cancel_refresh_loop()
        for t in list(_background_tasks):
            t.cancel()
        await app.state.proxy_http_client.aclose()


app = FastAPI(
    title="LineageLens Universal Proxy",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────────

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
    http_client: "httpx.AsyncClient | None" = None,
) -> Response:
    _own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0))
    try:
        upstream = await client.send(
            client.build_request(method, url, headers=headers, content=body),
            stream=True,
        )
    except Exception as exc:
        if _own_client:
            await client.aclose()
        logger.exception("upstream error: %s", exc)
        return Response(content="Bad gateway", status_code=502)

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
            if _own_client:
                await client.aclose()
            if skip_capture:
                pass
            elif upstream.status_code >= 400:
                logger.debug("skipping capture: upstream status %d", upstream.status_code)
            elif not _capture_overflow:
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
    http_client: "httpx.AsyncClient | None" = None,
) -> Response:
    _own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=120.0))
    try:
        upstream = await client.request(method, url, headers=headers, content=body)
    except Exception as exc:
        if _own_client:
            await client.aclose()
        logger.exception("upstream error: %s", exc)
        return Response(content="Bad gateway", status_code=502)
    if _own_client:
        await client.aclose()

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


async def _read_request_body(request: Request) -> "Response | bytes":
    """Read and size-limit the request body.

    Returns a Response (4xx) if the body is invalid or too large, otherwise
    returns the raw bytes.
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

    provider = detect_provider_from_inbound("/" + safe_path, dict(request.headers))
    url = _build_upstream_url_for_provider(provider, safe_path, request.url.query)

    if provider == "unknown":
        provider = detect_provider_and_format(url, dict(request.headers))

    logger.debug("provider routing: inbound=/%s → provider=%s upstream=%s", safe_path, provider, url)

    try:
        req_body_dict: dict | None = json.loads(body) if body else None
        if not isinstance(req_body_dict, dict):
            req_body_dict = None
    except Exception:
        req_body_dict = None

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

    file_path = extract_file_path(dict(request.headers), req_body_dict)

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
                        "savings_estimate_usd": 0.0,
                    }
                    logger.info(
                        "routing: %s → %s (tier=%s workspace=%s)",
                        current_model, target_model, tier, WORKSPACE_ID,
                    )
    except Exception as _routing_err:
        logger.warning("routing error (request forwarded unchanged): %s", _routing_err)

    _shared_client: httpx.AsyncClient | None = getattr(request.app.state, "proxy_http_client", None)

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
            http_client=_shared_client,
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
        http_client=_shared_client,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def _warn_if_unusual_upstream(url: str, var_name: str) -> None:
    """Log a warning when an upstream URL points to an unrecognized host."""
    if not url:
        return
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return
        if not any(host == d or host.endswith("." + d) for d in _KNOWN_LLM_DOMAINS):
            logger.warning(
                "%s points to an unrecognized host (%s). "
                "Client API keys (Authorization/x-api-key) will be forwarded there — "
                "verify this is the intended LLM endpoint.",
                var_name,
                host,
            )
    except Exception:
        pass


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
        logger.info("CONNECT  : port %d (%s)", PROXY_CONNECT_PORT,
                    "auth enabled" if PROXY_CONNECT_TOKEN else "no auth — set PROXY_CONNECT_TOKEN for production")

        _warn_if_unusual_upstream(UPSTREAM_URL, "UPSTREAM_URL")
        _warn_if_unusual_upstream(_ANTHROPIC_UPSTREAM_URL, "ANTHROPIC_UPSTREAM_URL")
        _warn_if_unusual_upstream(_OPENAI_UPSTREAM_URL, "OPENAI_UPSTREAM_URL")
        _warn_if_unusual_upstream(_GEMINI_UPSTREAM_URL, "GEMINI_UPSTREAM_URL")

        _assert_not_open_relay()

        if _backend_url_points_to_proxy():
            logger.error(
                "MISCONFIGURATION: BACKEND_URL (%s) points at the proxy itself — ingest calls would loop. "
                "Set BACKEND_URL to the LineageLens backend address.",
                BACKEND_URL,
            )
            raise SystemExit(1)

        if not INGEST_TOKEN:
            logger.warning("BACKEND_INGEST_TOKEN is not set — all AI captures will be skipped.")

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
