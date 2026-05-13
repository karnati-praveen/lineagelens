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
import urllib.parse
import uuid
from datetime import UTC, datetime

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
_host_cert_cache: dict[str, tuple[bytes, bytes]] = {}


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
    return {k: v for k, v in h.items() if k.lower() not in _DROP_REQ}


def _resp_headers(h) -> dict:
    return {k: v for k, v in h.items() if k.lower() not in _DROP_RESP}


def _is_streaming(body: bytes) -> bool:
    try:
        return bool(json.loads(body).get("stream", False))
    except Exception:
        return False


def _text_from_body(body: bytes) -> str:
    """Extract assistant text from a complete (non-streaming) JSON response."""
    try:
        data = json.loads(body)
    except Exception:
        return ""
    # OpenAI / compatible
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        pass
    # Anthropic
    try:
        parts = data["content"]
        if isinstance(parts, list):
            return "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
    except (KeyError, TypeError):
        pass
    return ""


def _text_from_sse(chunks: list[bytes]) -> str:
    """Reconstruct assistant text from a stream of SSE chunks."""
    parts: list[str] = []
    for chunk in chunks:
        try:
            raw = chunk.decode("utf-8", errors="replace")
        except Exception:
            continue
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("", "[DONE]"):
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # OpenAI streaming delta
            try:
                delta = data["choices"][0]["delta"].get("content") or ""
                parts.append(delta)
                continue
            except (KeyError, IndexError, TypeError):
                pass
            # Anthropic streaming delta
            if data.get("type") == "content_block_delta":
                parts.append(data.get("delta", {}).get("text", ""))
    return "".join(parts)


def _extract_code(text: str) -> str:
    """Pull out fenced code blocks; fall back to full text if none found."""
    blocks = re.findall(r"```\w*\n?(.*?)```", text, re.DOTALL)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks if b.strip())
    return text.strip()


# ── ingest ────────────────────────────────────────────────────────────────────

async def _ingest(text: str, upstream_path: str) -> None:
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
        "filePath": "proxy-capture",
        "insertedText": code,
        "workspaceId": WORKSPACE_ID,
        "provenance": {
            "source": "lineagelens-universal-proxy",
            "upstreamPath": upstream_path,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                f"{BACKEND_URL}/ingest",
                json=payload,
                headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
            )
        if r.status_code in (200, 201):
            logger.info("captured %d chars → backend", len(code))
        else:
            logger.warning("ingest %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        logger.warning("ingest error: %s", exc)


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
    method: str, url: str, headers: dict, body: bytes, safe_path: str
) -> Response:
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0))
    try:
        upstream = await client.send(
            client.build_request(method, url, headers=headers, content=body),
            stream=True,
        )
    except Exception as exc:
        await client.aclose()
        logger.error("upstream error: %s", exc)
        return Response(content=f"Upstream error: {exc}", status_code=502)

    collected: list[bytes] = []

    async def stream_gen():
        try:
            async for chunk in upstream.aiter_bytes():
                collected.append(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            text = _text_from_sse(collected)
            _task = asyncio.create_task(_ingest(text, f"/{safe_path}"))
            _background_tasks.add(_task)
            _task.add_done_callback(_background_tasks.discard)

    return StreamingResponse(
        stream_gen(),
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


async def _handle_non_streaming(
    method: str, url: str, headers: dict, body: bytes, safe_path: str
) -> Response:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=120.0)) as client:
            upstream = await client.request(method, url, headers=headers, content=body)
    except Exception as exc:
        logger.error("upstream error: %s", exc)
        return Response(content=f"Upstream error: {exc}", status_code=502)

    text = _text_from_body(upstream.content)
    if text:
        _task = asyncio.create_task(_ingest(text, f"/{safe_path}"))
        _background_tasks.add(_task)
        _task.add_done_callback(_background_tasks.discard)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_request(request: Request, path: str) -> Response:
    safe_path = _sanitize_path(path)
    url = _build_upstream_url(safe_path, request.url.query)

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
    body = b"".join(body_parts)

    headers = _fwd_headers(request.headers)
    if _is_streaming(body):
        return await _handle_streaming(request.method, url, headers, body, safe_path)
    return await _handle_non_streaming(request.method, url, headers, body, safe_path)


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
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle_connect_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one inbound CONNECT request."""
    import ssl as _ssl
    import tempfile as _tmpfile

    try:
        # Read request line: CONNECT host:port HTTP/1.1
        line = await asyncio.wait_for(client_reader.readline(), timeout=15.0)
        parts = line.rstrip(b"\r\n").split(b" ")
        if len(parts) < 2 or parts[0].upper() != b"CONNECT":
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
            return

        host_port = parts[1].decode("ascii", errors="replace")
        host, _, port_str = host_port.rpartition(":")
        port = int(port_str) if port_str.isdigit() else 443

        # Drain remaining request headers
        while True:
            hline = await asyncio.wait_for(client_reader.readline(), timeout=5.0)
            if hline in (b"\r\n", b"\n", b""):
                break

        # Connect to upstream (plain TCP — we add TLS in MITM mode ourselves)
        try:
            if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
                # MITM: connect to real server with TLS so we can forward decrypted traffic
                server_ctx = _ssl.create_default_context()
                up_reader, up_writer = await asyncio.open_connection(
                    host, port, ssl=server_ctx
                )
            else:
                up_reader, up_writer = await asyncio.open_connection(host, port)
        except Exception as exc:
            logger.warning("CONNECT upstream error %s:%s — %s", host, port, exc)
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            return

        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
            # MITM mode: upgrade the client side to TLS using the generated host cert
            cert_pem, key_pem = _generate_host_cert(host)

            cert_file = key_file = ""
            try:
                with _tmpfile.NamedTemporaryFile(delete=False, suffix=".pem") as cf:
                    cf.write(cert_pem)
                    cert_file = cf.name
                with _tmpfile.NamedTemporaryFile(delete=False, suffix=".pem") as kf:
                    kf.write(key_pem)
                    key_file = kf.name

                client_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
                client_ctx.load_cert_chain(cert_file, key_file)

                loop = asyncio.get_event_loop()
                transport = client_writer.transport
                protocol = transport.get_protocol()
                await loop.start_tls(transport, protocol, client_ctx, server_side=True)
                # After start_tls the StreamReader/Writer are updated in-place.
            except Exception as exc:
                logger.warning("MITM TLS upgrade failed for %s: %s — transparent fallback", host, exc)
                # Can't fall back to transparent here (server connection is TLS) — close
                up_writer.close()
                return
            finally:
                for f in (cert_file, key_file):
                    if f:
                        try:
                            os.unlink(f)
                        except OSError:
                            pass

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
        await asyncio.gather(server.serve(), _run_connect_server())

    asyncio.run(_main())
