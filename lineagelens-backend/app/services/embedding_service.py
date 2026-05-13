import hashlib
import json
from typing import TYPE_CHECKING
from urllib import error as url_error
from urllib import request as url_request

if TYPE_CHECKING:
    from app.core.config import Settings


async def generate_embedding(
    text: str, dimensions: int, settings: "Settings | None" = None
) -> list[float]:
    """Generate an embedding vector.

    Uses the OpenAI embeddings API when EMBEDDING_PROVIDER=openai and
    EMBEDDING_API_KEY is set; falls back to a deterministic hash otherwise.
    """
    if settings is not None and getattr(settings, "embedding_provider", "hash") == "openai":
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

    return _hash_embedding(text, dimensions)


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
