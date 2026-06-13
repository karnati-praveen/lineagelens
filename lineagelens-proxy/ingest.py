"""Backend ingest calls and content redaction for the LineageLens proxy."""
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime

import httpx

from config import (
    BACKEND_URL,
    INGEST_TOKEN,
    WORKSPACE_ID,
    REDACT_PATTERNS,
    _backend_url_points_to_proxy,
)

logger = logging.getLogger("lineagelens-proxy")

# Shared client for all backend ingest calls — avoids a TCP+TLS handshake per capture.
# Created on first use; never explicitly closed (process lifetime).
_ingest_http_client: httpx.AsyncClient | None = None


def _get_ingest_client() -> httpx.AsyncClient:
    global _ingest_http_client
    if _ingest_http_client is None:
        _ingest_http_client = httpx.AsyncClient(timeout=10.0)
    return _ingest_http_client


def _redact(text: str) -> str:
    """Apply REDACT_PATTERNS to text before it is sent to the ingest backend."""
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


_LICENSE_SHINGLE_K = 5
_LICENSE_SCAN_MAX_CHARS = 50_000  # hard cap — never fingerprint huge blobs


def _compute_shingles(code: str, k: int = _LICENSE_SHINGLE_K) -> list[int]:
    """Compute k-gram shingle hashes for *code* (same algorithm as the backend).

    Returns a list of int32 hashes suitable for JSON serialisation.
    Returns [] on any error so callers can safely skip the field.
    Uses stable SHA-256-derived hashes (not Python's hash()) for cross-process
    reproducibility.
    """
    try:
        # Normalise: strip comments, lowercase, tokenize on word chars
        c = re.sub(r"//[^\n]*", " ", code)
        c = re.sub(r"/\*.*?\*/", " ", c, flags=re.DOTALL)
        c = re.sub(r"#[^\n]*", " ", c)
        tokens = re.findall(r"\w+", c.lower())
        if not tokens:
            return []
        if len(tokens) < k:
            gram_str = " ".join(tokens)
            return [int(hashlib.sha256(gram_str.encode()).hexdigest()[:8], 16)]
        seen: set[int] = set()
        for i in range(len(tokens) - k + 1):
            gram_str = " ".join(tokens[i : i + k])
            h = int(hashlib.sha256(gram_str.encode()).hexdigest()[:8], 16)
            seen.add(h)
        return list(seen)
    except Exception:
        return []


def _license_scan(code: str) -> dict | None:
    """Build a licenseScan payload for the backend, or None on error / empty code.

    Non-blocking: any exception returns None so the caller can omit the field.
    Size-bounded: fingerprinting is skipped for very large code blocks.
    """
    try:
        if not code or len(code) > _LICENSE_SCAN_MAX_CHARS:
            return None
        shingles = _compute_shingles(code)
        if not shingles:
            return None
        return {"shingles": shingles, "k": _LICENSE_SHINGLE_K}
    except Exception:
        return None


def _extract_code(text: str) -> str:
    """Pull out fenced code blocks; fall back to full text if none found."""
    blocks = re.findall(r"```(?:\w+)?\n?(.*?)```", text, re.DOTALL)
    non_empty = [b.strip() for b in blocks if b.strip()]
    if non_empty:
        return "\n\n".join(non_empty)
    return text.strip()


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
    routing_info = edit.get("_routing")
    if routing_info:
        payload["routing"] = routing_info
    # F5: attach license fingerprint — fail-open
    try:
        scan = _license_scan(new_string)
        if scan:
            payload["licenseScan"] = scan
    except Exception as _lse:
        logger.debug("License scan skipped (edit): %s", _lse)

    try:
        resp = await _get_ingest_client().post(
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


async def _ingest_agent_actions(
    actions: list[dict],
    session_key: str,
    prompt_context_id: str,
) -> None:
    """Send captured agent actions to the backend action ledger.

    Fail-open: any error is logged but never propagates to the caller so the
    existing proxy forwarding path is never disrupted.
    """
    if not actions:
        return
    if _backend_url_points_to_proxy():
        logger.error(
            "BACKEND_URL points at the proxy itself — skipping agent action capture to avoid loop."
        )
        return
    if not INGEST_TOKEN:
        logger.debug("BACKEND_INGEST_TOKEN not configured — skipping agent action capture")
        return

    payload = {
        "workspaceId": WORKSPACE_ID,
        "sessionKey": session_key,
        "promptContextId": prompt_context_id or None,
        "actions": actions,
    }
    try:
        resp = await _get_ingest_client().post(
            f"{BACKEND_URL}/agent-actions",
            json=payload,
            headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        )
        resp.raise_for_status()
        logger.info(
            "agent actions captured: %d action(s) for session %s",
            len(actions),
            session_key[:8],
        )
    except Exception as exc:
        logger.error(
            "Failed to deliver agent actions to backend: %s %s",
            type(exc).__name__,
            exc,
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
    # F5: attach license fingerprint — fail-open: never break forwarding on error
    try:
        scan = _license_scan(code)
        if scan:
            payload["licenseScan"] = scan
    except Exception as _lse:
        logger.debug("License scan skipped: %s", _lse)

    try:
        resp = await _get_ingest_client().post(
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
