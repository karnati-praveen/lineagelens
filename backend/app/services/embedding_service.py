import hashlib


async def generate_embedding(text: str, dimensions: int) -> list[float]:
    """Generate a deterministic embedding vector.

    This placeholder can be swapped with an external embedding provider
    without changing the backend API contracts.
    """

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
