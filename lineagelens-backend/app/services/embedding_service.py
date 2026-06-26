from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING
from urllib import error as url_error
from urllib import request as url_request

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

# Providers that produce *semantically meaningful* vectors. The default "hash"
# provider is deterministic but carries NO semantic signal — cosine search over
# hash vectors returns arbitrary neighbours. PART 3 #18: never present that as
# semantic search; callers must check semantic_provider_active() and surface
# `semantic_search_unavailable` instead of faking quality.
_SEMANTIC_PROVIDERS = frozenset({"openai", "local"})

SEMANTIC_UNAVAILABLE_WARNING = (
    "semantic_search_unavailable: no semantic embedding provider is configured "
    "(EMBEDDING_PROVIDER='hash' produces non-semantic vectors). Falling back to "
    "keyword search. Set EMBEDDING_PROVIDER=openai (+ EMBEDDING_API_KEY) or "
    "EMBEDDING_PROVIDER=local to enable real semantic search."
)


def semantic_provider_active(settings: "Settings | None") -> bool:
    """True only when a provider that yields semantically meaningful vectors is usable.

    openai → requires an API key. local → requires the optional local model dep.
    Everything else (including the default "hash") is NOT semantic.
    """
    if settings is None:
        return False
    provider = getattr(settings, "embedding_provider", "hash")
    if provider == "openai":
        return bool((getattr(settings, "embedding_api_key", None) or "").strip())
    if provider == "local":
        return _local_model_available()
    return False


def _local_model_available() -> bool:
    """Whether an offline local embedding model can be loaded (optional dependency)."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


async def generate_embedding(
    text: str, dimensions: int, settings: "Settings | None" = None
) -> list[float]:
    """Generate an embedding vector.

    Uses a real semantic provider (OpenAI or a local model) when configured;
    otherwise falls back to a deterministic *non-semantic* hash. The hash vector
    is fine for storage/dedup but must never be used to claim semantic search —
    see semantic_provider_active() / PART 3 #18.
    """
    provider = getattr(settings, "embedding_provider", "hash") if settings is not None else "hash"

    if provider == "openai":
        api_key = (getattr(settings, "embedding_api_key", None) or "").strip()
        if api_key:
            result = await _openai_embedding(
                text=text,
                dimensions=dimensions,
                api_url=getattr(
                    settings,
                    "embedding_api_url",
                    "https://api.openai.com/v1/embeddings",
                ),
                api_key=api_key,
                model=getattr(settings, "embedding_model_name", "text-embedding-3-small"),
            )
            if result is not None:
                return result
    elif provider == "local":
        result = await _local_embedding(text, dimensions, settings)
        if result is not None:
            return result

    return _hash_embedding(text, dimensions)


async def _local_embedding(
    text: str, dimensions: int, settings: "Settings | None"
) -> list[float] | None:
    """Offline embedding via sentence-transformers, if installed. Returns None otherwise."""
    import asyncio

    def _run() -> list[float] | None:
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            return None
        try:
            model_name = getattr(settings, "embedding_model_name", "all-MiniLM-L6-v2")
            model = _load_local_model(model_name)
            vector = model.encode(text or " ").tolist()
        except Exception as exc:
            logger.warning("Local embedding failed: %s", exc)
            return None
        if len(vector) >= dimensions:
            return [float(v) for v in vector[:dimensions]]
        return [float(v) for v in vector] + [0.0] * (dimensions - len(vector))

    return await asyncio.to_thread(_run)


def _load_local_model(model_name: str):
    from functools import lru_cache

    @lru_cache(maxsize=2)
    def _cached(name: str):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(name)

    return _cached(model_name)


async def _openai_embedding(
    *,
    text: str,
    dimensions: int,
    api_url: str,
    api_key: str,
    model: str,
) -> list[float] | None:
    """Call an OpenAI-compatible embeddings endpoint synchronously via asyncio.to_thread."""
    import asyncio

    return await asyncio.to_thread(
        _call_embedding_sync, text, dimensions, api_url, api_key, model
    )


def _call_embedding_sync(
    text: str, dimensions: int, api_url: str, api_key: str, model: str
) -> list[float] | None:
    body = json.dumps(
        {"model": model, "input": text or " ", "dimensions": dimensions}
    ).encode("utf-8")

    req = url_request.Request(
        api_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with url_request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (url_error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    try:
        vector: list[float] = payload["data"][0]["embedding"]
        if len(vector) == dimensions:
            return vector
        # Truncate or pad to match expected dimensions
        if len(vector) > dimensions:
            return vector[:dimensions]
        return vector + [0.0] * (dimensions - len(vector))
    except (KeyError, IndexError, TypeError):
        return None


def _hash_embedding(text: str, dimensions: int) -> list[float]:
    dims = max(8, dimensions)
    source = text or ""
    if not source.strip():
        return [0.0] * dims

    result: list[float] = []
    counter = 0

    while len(result) < dims:
        digest = hashlib.sha256(f"{counter}:{source}".encode("utf-8")).digest()
        counter += 1

        for byte in digest:
            normalized = (byte / 127.5) - 1.0
            result.append(round(normalized, 6))
            if len(result) >= dims:
                break

    return result
