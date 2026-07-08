"""Shared state and utilities for all provider adapters."""
import asyncio
import json
import logging
import sys
import time

logger = logging.getLogger("lineagelens-proxy")

# SSE protocol markers shared across all provider parsers.
_SSE_DATA_PREFIX = "data:"
_SSE_DONE_MARKER = "[DONE]"

# Explicit phrases that signal a *user* (not system) rejection of a proposed
# edit. A bare "user " substring is intentionally NOT used here: benign error
# text like "another user connected" or "user token expired" would otherwise be
# misclassified as a rejected edit (CODE-02).
_REJECTION_PHRASES = (
    "user rejected",
    "user denied",
    "user declined",
    "user cancelled",
    "user canceled",
    "rejected by user",
    "denied by user",
    "permission denied",
    "operation not permitted",
    "edit was rejected",
    "change was rejected",
)


def _looks_like_rejection(lower_content: str) -> bool:
    """Return True iff *lower_content* (already lowercased) reads as a user rejection.

    Matches explicit rejection phrases instead of broad single-word substrings so
    that ordinary error messages containing the word "user" are not misread as
    rejections.
    """
    return any(phrase in lower_content for phrase in _REJECTION_PHRASES)

# Background tasks set — shared between proxy.py (lifespan cleanup) and all adapters.
_background_tasks: set[asyncio.Task] = set()

# Pending edits store: keyed by (session_key, tool_use_id).
_pending_edits: dict[tuple[str, str], list[dict]] = {}
_pending_edits_lock = asyncio.Lock()
_PENDING_EDITS_TTL_SECONDS = 3600   # drop unresolved proposals after 1 hour
_PENDING_EDITS_MAX = 5000            # hard cap to prevent unbounded growth


def _get_ingest_fn():
    """Return _ingest_edit, preferring the proxy module's attribute for test monkeypatching.

    Tests patch proxy._ingest_edit = fake after importing proxy. Because
    _resolve_pending_edits (and its codex/gemini variants) live in sub-modules,
    they must resolve _ingest_edit dynamically at call time via sys.modules so
    the test patch propagates without circular imports.
    """
    proxy_mod = sys.modules.get("proxy")
    if proxy_mod is not None:
        fn = getattr(proxy_mod, "_ingest_edit", None)
        if fn is not None:
            return fn
    from ingest import _ingest_edit  # direct import fallback
    return _ingest_edit


def _annotate_edits(
    edits: list[dict],
    ctx: dict,
    routing_info: dict | None,
    proposed_at: float,
) -> None:
    """Annotate a list of edit dicts with context metadata in-place."""
    for edit in edits:
        edit["_proposed_at"] = proposed_at
        edit["_model"] = ctx.get("model", "")
        edit["_system"] = ctx.get("system", "")
        edit["_messages"] = ctx.get("messages", [])
        if routing_info:
            edit["_routing"] = routing_info


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


# ── SSE / response-body text extraction helpers ───────────────────────────────

def _delta_from_sse_payload(payload: str, provider: str = "unknown") -> str:
    """Extract the text delta from a single parsed SSE data payload."""
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
    data: line never silently drops a text delta.
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
