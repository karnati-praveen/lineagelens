"""HTTPS CONNECT tunnel server for the LineageLens proxy.

Handles CONNECT requests from tools that use HTTPS_PROXY / HTTP_PROXY env vars.
Supports both transparent relay (default) and TLS MITM interception when CA
cert/key paths are configured.
"""
import asyncio
import ipaddress
import logging
import os
import socket
import sys as _sys
import tempfile
from http import HTTPStatus

import anyio
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from config import (
    _BLOCKED_CONNECT_HOSTS,
    MAX_BODY_BYTES,
    PROXY_CA_CERT_PATH,
    PROXY_CA_KEY_PATH,
    PROXY_CONNECT_PORT,
    PROXY_CONNECT_TOKEN,
    PROXY_HOST,
)

logger = logging.getLogger("lineagelens-proxy")

# Per-host cert cache for MITM mode; capped at 500 entries.
_HOST_CERT_CACHE_MAX = 500
_host_cert_cache: dict[str, tuple[bytes, bytes]] = {}


def _is_internal_addr(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    )


async def _resolve_and_validate_host(host: str) -> str | None:
    """Resolve *host* to a pinned IP, blocking if any resolved address is internal.

    Returns the pinned IP string to connect to, or None if the host is blocked.
    Connecting to the returned IP (rather than the original hostname) eliminates
    the DNS-rebinding window: the resolution result is validated once and reused.
    """
    host_lower = host.strip().lower()
    if host_lower.startswith("[") and host_lower.endswith("]"):
        host_lower = host_lower[1:-1]
    host_lower = host_lower.split("%")[0]

    if host_lower in _BLOCKED_CONNECT_HOSTS:
        return None

    # Literal IP address — validate directly without a DNS call.
    try:
        addr = ipaddress.ip_address(host_lower)
        if _is_internal_addr(addr):
            return None
        return host_lower
    except ValueError:
        pass

    # Hostname — resolve every address and reject if any is internal.
    try:
        loop = asyncio.get_running_loop()
        results: list = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host_lower, None, type=socket.SOCK_STREAM),
        )
    except OSError:
        return None

    if not results:
        return None

    pinned_ip: str | None = None
    for result in results:
        raw_ip = result[4][0].split("%")[0]  # strip IPv6 scope ID
        try:
            addr = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if _is_internal_addr(addr):
            return None  # any resolved address internal → block
        if pinned_ip is None:
            pinned_ip = raw_ip

    return pinned_ip


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
) -> tuple[str, str, int] | None:
    """Read and parse the CONNECT request, drain headers, enforce auth + host checks.

    Returns (host, pinned_ip, port) on success, or None after writing an error
    response.  *host* is the original requested hostname (used for TLS SNI in
    MITM mode); *pinned_ip* is the validated, pre-resolved address to connect to
    (prevents DNS-rebinding between allow-check and the actual TCP connect).
    """
    import hmac

    line = await asyncio.wait_for(reader.readline(), timeout=15.0)
    parts = line.rstrip(b"\r\n").split(b" ")
    if len(parts) < 2 or parts[0].upper() != b"CONNECT":
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return None

    host_port = parts[1].decode("ascii", errors="replace")
    host, _, port_str = host_port.rpartition(":")
    port = int(port_str) if port_str.isdigit() else 443

    proxy_auth_value = ""
    for _ in range(100):
        hline = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if hline in (b"\r\n", b"\n", b""):
            break
        if b":" in hline:
            name_b, _, val_b = hline.partition(b":")
            if name_b.strip().lower() == b"proxy-authorization":
                proxy_auth_value = val_b.strip().decode("latin-1", errors="replace")
    else:
        writer.write(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
        await writer.drain()
        return None

    if PROXY_CONNECT_TOKEN:
        expected = f"Bearer {PROXY_CONNECT_TOKEN}"
        if not hmac.compare_digest(proxy_auth_value, expected):
            writer.write(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b"Proxy-Authenticate: Bearer realm=\"LineageLens\"\r\n"
                b"content-length: 0\r\n\r\n"
            )
            await writer.drain()
            return None

    pinned_ip = await _resolve_and_validate_host(host)
    if pinned_ip is None:
        logger.warning("CONNECT to internal/unresolvable host blocked: %s:%d", host, port)
        writer.write(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\n\r\n")
        await writer.drain()
        return None

    return host, pinned_ip, port


async def _connect_to_upstream(
    host: str,
    pinned_ip: str,
    port: int,
    writer: asyncio.StreamWriter,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Open a TCP (or TLS) connection to the upstream host.

    Connects to *pinned_ip* (the pre-validated, pre-resolved address) rather
    than re-resolving *host*, which eliminates the DNS-rebinding window between
    the SSRF check and the actual connect syscall.  *host* is kept for the TLS
    SNI field so the upstream server still presents the correct certificate.
    """
    import ssl as _ssl

    try:
        if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
            server_ctx = _ssl.create_default_context()
            up_reader, up_writer = await asyncio.open_connection(
                pinned_ip, port, ssl=server_ctx, server_hostname=host
            )
        else:
            up_reader, up_writer = await asyncio.open_connection(pinned_ip, port)
    except Exception as exc:
        logger.warning("CONNECT upstream error %s (%s):%s — %s", host, pinned_ip, port, exc)
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        return None

    return up_reader, up_writer


async def _write_temp_pem(data: bytes, suffix: str = ".pem") -> str:
    """Write data to a new temporary file and return its path."""
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
    """Upgrade the inbound client connection to TLS for MITM interception."""
    import ssl as _ssl

    cert_pem, key_pem = _generate_host_cert(host)
    cert_file = key_file = ""
    try:
        cert_file = await _write_temp_pem(cert_pem, suffix=".pem")
        key_file = await _write_temp_pem(key_pem, suffix=".pem")

        client_ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        client_ctx.minimum_version = _ssl.TLSVersion.TLSv1_2
        client_ctx.load_cert_chain(cert_file, key_file)

        loop = asyncio.get_running_loop()
        transport = client_writer.transport
        protocol = transport.get_protocol()
        await loop.start_tls(transport, protocol, client_ctx, server_side=True)
        return True
    except Exception as exc:
        logger.warning("MITM TLS upgrade failed for %s: %s", host, exc)
        return False
    finally:
        for f in (cert_file, key_file):
            if f:
                try:
                    os.unlink(f)
                except OSError:
                    pass


# ── HTTP helpers for MITM ─────────────────────────────────────────────────────

def _split_http_target(target: str) -> tuple[str, str]:
    import urllib.parse as _up
    parsed = _up.urlsplit(target)
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
    # Late import via sys.modules to avoid circular dependency: connect_tunnel
    # is imported by proxy, and proxy_request lives in proxy.
    _proxy_mod = _sys.modules["proxy"]

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
            response = await _proxy_mod.proxy_request(request, route_path)
        except Exception as exc:
            logger.exception("MITM proxy request failed for %s %s: %s", method, target, exc)
            await _write_plain_http_error(client_writer, 502, "Bad Gateway")
            return

        try:
            await _write_http_response(client_writer, response, method)
        except Exception as exc:
            logger.debug("MITM response relay failed for %s %s: %s", method, target, exc)
            return


async def _handle_connect_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one inbound CONNECT request."""
    try:
        result = await _parse_connect_request(client_reader, client_writer)
        if result is None:
            return
        host, pinned_ip, port = result

        client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_writer.drain()

        if PROXY_CA_CERT_PATH and PROXY_CA_KEY_PATH:
            ok = await _mitm_upgrade_client_tls(host, client_writer)
            if not ok:
                return

            await _handle_mitm_http_requests(client_reader, client_writer, host, port)
            return

        up_pair = await _connect_to_upstream(host, pinned_ip, port, client_writer)
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


def _assert_not_open_relay() -> None:
    """Abort startup if the proxy would be an unauthenticated open relay.

    An open relay occurs when the proxy binds to a non-loopback interface
    (i.e. is network-reachable) AND PROXY_CONNECT_TOKEN is empty, meaning
    anyone on the network can proxy arbitrary connections through it.
    Loopback-only binds without a token are fine for local development.
    """
    loopback_prefixes = ("127.", "::1")
    is_loopback = PROXY_HOST in ("localhost",) or any(
        PROXY_HOST.startswith(p) for p in loopback_prefixes
    )
    if not is_loopback and not PROXY_CONNECT_TOKEN:
        raise SystemExit(
            "FATAL: PROXY_HOST is set to a non-loopback address "
            f"({PROXY_HOST!r}) but PROXY_CONNECT_TOKEN is empty. "
            "Binding without a token would create an open CONNECT relay "
            "accessible to the network. "
            "Either set PROXY_CONNECT_TOKEN or restrict PROXY_HOST to "
            "127.0.0.1 (loopback)."
        )


async def _run_connect_server() -> None:
    """Run the HTTPS CONNECT tunnel server on PROXY_CONNECT_PORT."""
    _assert_not_open_relay()
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
