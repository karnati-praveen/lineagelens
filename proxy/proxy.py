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
import re
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

_DROP_REQ  = {"host", "content-length", "transfer-encoding", "connection"}
_DROP_RESP = {"content-encoding", "transfer-encoding", "connection", "content-length"}

app = FastAPI(title="LineageLens Universal Proxy", docs_url=None, redoc_url=None)


# ── helpers ───────────────────────────────────────────────────────────────────

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
    blocks = re.findall(r"```(?:\w*)\n?(.*?)```", text, re.DOTALL)
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

    code = _extract_code(text)
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
            logger.info("captured %d chars → backend (%s)", len(code), upstream_path)
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


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_request(request: Request, path: str) -> Response:
    url = f"{UPSTREAM_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    body    = await request.body()
    headers = _fwd_headers(request.headers)

    # ── streaming ─────────────────────────────────────────────────────────────
    if _is_streaming(body):
        client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0))
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
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
                asyncio.create_task(_ingest(text, f"/{path}"))

        return StreamingResponse(
            stream_gen(),
            status_code=upstream.status_code,
            headers=_resp_headers(upstream.headers),
            media_type=upstream.headers.get("content-type", "text/event-stream"),
        )

    # ── non-streaming ─────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=120.0)) as client:
            upstream = await client.request(
                request.method, url, headers=headers, content=body
            )
    except Exception as exc:
        logger.error("upstream error: %s", exc)
        return Response(content=f"Upstream error: {exc}", status_code=502)

    text = _text_from_body(upstream.content)
    if text:
        asyncio.create_task(_ingest(text, f"/{path}"))

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_resp_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logger.info("LineageLens Universal Proxy starting on port %d", PROXY_PORT)
    logger.info("Upstream : %s", UPSTREAM_URL)
    logger.info("Backend  : %s", BACKEND_URL)
    logger.info("Workspace: %s", WORKSPACE_ID)
    logger.info("Token    : %s", "configured" if INGEST_TOKEN else "NOT SET — captures will be skipped")
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="info")
