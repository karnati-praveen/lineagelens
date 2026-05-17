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
import json
import logging
import os
import posixpath
import re
import tempfile
import urllib.parse
import uuid
from datetime import UTC, datetime

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lineagelens-proxy")

UPSTREAM_URL      = os.environ.get("UPSTREAM_URL", "https://api.anthropic.com").rstrip("/")
BACKEND_URL       = os.environ.get("BACKEND_URL", "http://backend:8787").rstrip("/")
INGEST_TOKEN      = os.environ.get("BACKEND_INGEST_TOKEN", "")
WORKSPACE_ID      = os.environ.get("PROXY_WORKSPACE_ID", "proxy-capture")
PROXY_PORT        = int(os.environ.get("PROXY_PORT", "8788"))
PROXY_HOST        = os.environ.get("PROXY_HOST", "0.0.0.0")
MAX_BODY_BYTES    = int(os.environ.get("PROXY_MAX_BODY_BYTES", "2000000"))
# Comma-separated regex patterns to redact from captured content before ingest.
# Example: PROXY_REDACT_PATTERNS="Bearer [A-Za-z0-9._-]+,sk-[A-Za-z0-9]+"
REDACT_PATTERNS       = [re.compile(p.strip()) for p in os.environ.get("PROXY_REDACT_PATTERNS", "").split(",") if p.strip()]
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

_DROP_REQ  = {"host", "content-length", "transfer-encoding", "connection"}
_DROP_RESP = {"content-encoding", "transfer-encoding", "connection", "content-length"}

app = FastAPI(title="LineageLens Universal Proxy", docs_url=None, redoc_url=None)


# ── helpers ───────────────────────────────────────────────────────────────────

def _redact(text: str) -> str:
    """Apply PROXY_REDACT_PATTERNS to text before it is sent to the ingest backend."""
    for pattern in REDACT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _fwd_headers(h) -> dict:
    forwarded = {k: v for k, v in h.items() if k.lower() not in _DROP_REQ}
    if "authorization" in {k.lower() for k in forwarded}:
        logger.debug("Forwarding Authorization header to upstream — ensure client API keys are intended for %s", UPSTREAM_URL)
    return forwarded


def _resp_headers(h) -> dict:
    return {k: v for k, v in h.items() if k.lower() not in _DROP_RESP}


def _is_streaming(body: bytes) -> bool:
    try:
        return bool(json.loads(body).get("stream", False))
    except Exception:
        return False


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
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
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
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            delta = _delta_from_sse_payload(payload, provider)
            if delta:
                parts.append(delta)
    if remainder:
        line = remainder.rstrip("\r")
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload not in ("", "[DONE]"):
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


# ── ingest ────────────────────────────────────────────────────────────────────

async def _ingest(
    text: str,
    upstream_path: str,
    provider: str = "unknown",
    file_path: str = "proxy-capture",
    upstream_method: str = "POST",
    upstream_status: int = 200,
) -> None:
    if not text.strip():
        return
    if not INGEST_TOKEN:
        logger.debug("BACKEND_INGEST_TOKEN not configured — skipping capture")
        return

    code = _redact(_extract_code(text))
    if not code:
        return

    payload = {
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
    return {
        "status": "ok",
        "upstream": UPSTREAM_URL,
        "backend": BACKEND_URL,
        "workspace": WORKSPACE_ID,
        "tokenConfigured": bool(INGEST_TOKEN),
    }


async def _handle_streaming(
    method: str,
    url: str,
    headers: dict,
    body: bytes,
    safe_path: str,
    provider: str = "unknown",
    file_path: str = "proxy-capture",
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
        return Response(content=f"Upstream error: {exc}", status_code=502)

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
                    collected.append(chunk)
                    _collected_bytes += len(chunk)
                    if _collected_bytes > MAX_BODY_BYTES:
                        _capture_overflow = True
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            if skip_capture:
                pass  # already logged above
            elif upstream.status_code >= 400:
                logger.debug("skipping capture: upstream status %d", upstream.status_code)
            elif not _capture_overflow:
                text = _text_from_sse(collected, provider)
                _task = asyncio.create_task(
                    _ingest(
                        text, f"/{safe_path}", provider=provider, file_path=file_path,
                        upstream_method=method, upstream_status=upstream.status_code,
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
) -> Response:
    # URL base is from UPSTREAM_URL (env-configured, trusted); only path is from request.
    # Scheme and host are pinned by _build_upstream_url — never sourced from user input.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=120.0)) as client:
            upstream = await client.request(method, url, headers=headers, content=body)
    except Exception as exc:
        logger.exception("upstream error: %s", exc)
        return Response(content=f"Upstream error: {exc}", status_code=502)

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
            text = _text_from_body(upstream.content, provider=provider)
            if text:
                _task = asyncio.create_task(
                    _ingest(
                        text, f"/{safe_path}", provider=provider, file_path=file_path,
                        upstream_method=method, upstream_status=upstream.status_code,
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
    url = _build_upstream_url(safe_path, request.url.query)

    body_or_error = await _read_request_body(request)
    if isinstance(body_or_error, Response):
        return body_or_error
    body: bytes = body_or_error

    headers = _fwd_headers(request.headers)

    # Detect provider from the resolved upstream URL and request headers.
    provider = detect_provider_and_format(url, dict(request.headers))

    # Extract the best available file path from request metadata.
    try:
        req_body_dict = json.loads(body) if body else None
    except Exception:
        req_body_dict = None
    file_path = extract_file_path(dict(request.headers), req_body_dict)

    if _is_streaming(body):
        return await _handle_streaming(
            request.method, url, headers, body, safe_path,
            provider=provider, file_path=file_path,
        )
    return await _handle_non_streaming(
        request.method, url, headers, body, safe_path,
        provider=provider, file_path=file_path,
    )


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
            up_reader, up_writer = await asyncio.open_connection(
                host, port, ssl=server_ctx
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
    up_writer: asyncio.StreamWriter,
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
            "MITM TLS upgrade failed for %s: %s — transparent fallback", host, exc
        )
        up_writer.close()
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

        up_pair = await _connect_to_upstream(host, port, client_writer)
        if up_pair is None:
            return
        up_reader, up_writer = up_pair

        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
            ok = await _mitm_upgrade_client_tls(host, client_writer, up_writer)
            if not ok:
                return

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
        _proxy_self = f"http://{PROXY_HOST}:{PROXY_PORT}"
        if BACKEND_URL.rstrip("/") in (_proxy_self, f"http://localhost:{PROXY_PORT}", f"http://127.0.0.1:{PROXY_PORT}"):
            logger.error(
                "MISCONFIGURATION: BACKEND_URL (%s) points at the proxy itself — ingest calls would loop. "
                "Set BACKEND_URL to the LineageLens backend address.",
                BACKEND_URL,
            )

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
