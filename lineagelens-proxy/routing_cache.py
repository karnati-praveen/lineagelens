"""
Routing policy cache for the LineageLens proxy.

Fetches workspace routing policies from the backend and holds them in
an in-memory dict keyed by (workspace_id, provider).  A background task
refreshes the cache every ROUTING_CACHE_TTL_SECONDS seconds.

Usage
-----
    from routing_cache import init_routing_cache, get_policy

    # Call once at app startup:
    await init_routing_cache()

    # Per-request:
    policy = await get_policy(workspace_id, "anthropic")
    if policy and policy["enabled"]:
        target_model = policy["mappings"].get(tier)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

logger = logging.getLogger("lineagelens-proxy.routing_cache")

# NOTE: BACKEND_URL and INGEST_TOKEN mirror the same env vars in proxy.py.
# They are defined here independently to avoid a circular import.
# TODO: extract to a shared constants module (e.g. proxy_constants.py) if the
#       number of duplicated env vars grows.
BACKEND_URL   = os.environ.get("BACKEND_URL",          "http://backend:8787").rstrip("/")
INGEST_TOKEN  = os.environ.get("BACKEND_INGEST_TOKEN", "")
ROUTING_CACHE_TTL_SECONDS = int(os.environ.get("ROUTING_CACHE_TTL_SECONDS", "60"))

# (workspace_id, provider) → {"policy": dict, "fetched_at": float}
_cache: dict[tuple[str, str], dict] = {}
_cache_lock = asyncio.Lock()

# Background refresh task handle — kept to cancel on shutdown.
_refresh_task: asyncio.Task | None = None


async def _fetch_all_policies() -> list[dict]:
    """Call GET /policies/routing/internal and return a list of policy dicts."""
    if not INGEST_TOKEN:
        logger.debug("BACKEND_INGEST_TOKEN not configured — skipping routing policy fetch")
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{BACKEND_URL}/policies/routing/internal",
                headers={"X-Backend-Token": INGEST_TOKEN},
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            return data.get("policies", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.warning("routing cache: failed to fetch policies from backend: %s", exc)
        return []


async def refresh_all() -> None:
    """Fetch all enabled routing policies and replace the cache."""
    policies = await _fetch_all_policies()
    now = time.monotonic()
    async with _cache_lock:
        # Keep stale entries for workspace+providers NOT returned (policy might
        # have just been temporarily unavailable) — only overwrite what we got.
        for p in policies:
            workspace_id = p.get("workspaceId") or p.get("workspace_id", "")
            provider = p.get("provider", "")
            if workspace_id and provider:
                _cache[(workspace_id, provider)] = {"policy": p, "fetched_at": now}
    logger.debug("routing cache: refreshed %d policies", len(policies))


async def _refresh_loop() -> None:
    """Background task: refresh the routing policy cache every TTL seconds."""
    while True:
        await asyncio.sleep(ROUTING_CACHE_TTL_SECONDS)
        try:
            await refresh_all()
        except Exception as exc:
            logger.warning("routing cache refresh loop error: %s", exc)


async def init_routing_cache() -> None:
    """Load policies once at startup and start the background refresh loop."""
    global _refresh_task
    await refresh_all()
    _refresh_task = asyncio.create_task(_refresh_loop())
    _refresh_task.add_done_callback(
        lambda t: logger.warning("routing cache refresh loop exited: %s", t.exception())
        if not t.cancelled() and t.exception() else None
    )
    logger.info("routing cache initialised (TTL=%ds)", ROUTING_CACHE_TTL_SECONDS)


async def get_policy(workspace_id: str, provider: str) -> dict | None:
    """Return the cached routing policy for (workspace_id, provider), or None.

    If the cached entry is stale (> TTL seconds old), triggers a lazy refresh
    before returning the (possibly stale) value so the current request is not
    blocked.  The next request will get the fresh value.
    """
    if not workspace_id or not provider:
        return None

    key = (workspace_id, provider)
    now = time.monotonic()

    async with _cache_lock:
        entry = _cache.get(key)

    if entry is None:
        # First time we see this workspace+provider: fetch eagerly (blocks
        # current request, but only happens once per combination).
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{BACKEND_URL}/policies/routing/internal",
                    params={"workspace_id": workspace_id, "provider": provider},
                    headers={"X-Backend-Token": INGEST_TOKEN},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    policies = data.get("policies", []) if isinstance(data, dict) else []
                    fetched = time.monotonic()
                    async with _cache_lock:
                        for p in policies:
                            ws  = p.get("workspaceId") or p.get("workspace_id", "")
                            pv  = p.get("provider", "")
                            if ws and pv:
                                _cache[(ws, pv)] = {"policy": p, "fetched_at": fetched}
                        # Read back inside the same critical section to avoid a second acquisition.
                        entry = _cache.get(key)
        except Exception as exc:
            logger.debug("routing cache: per-workspace fetch failed: %s", exc)

    if entry is None:
        return None

    # Trigger background refresh if stale but don't block.
    if now - entry["fetched_at"] > ROUTING_CACHE_TTL_SECONDS:
        asyncio.create_task(refresh_all())

    policy = entry["policy"]
    # Only return enabled policies.
    return policy if policy.get("enabled") else None


def cancel_refresh_loop() -> None:
    """Cancel the background refresh task (called on app shutdown)."""
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
