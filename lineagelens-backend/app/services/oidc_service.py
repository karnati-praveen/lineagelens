from __future__ import annotations

import ipaddress
import logging
import time
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

_STATE_TTL = 300  # 5 minutes

# Hostnames that must never be used as OIDC issuer or endpoint targets.
_OIDC_BLOCKED_NAMES = frozenset({
    "localhost", "0.0.0.0", "::1",
    "169.254.169.254",           # AWS / Azure IMDS
    "metadata.google.internal",  # GCP metadata
    "metadata.internal",         # generic cloud metadata alias
})


def _validate_oidc_url(url: str, label: str = "URL") -> None:
    """Validate that *url* is safe to use as an OIDC endpoint.

    Raises :class:`ValueError` if:
    - the URL is empty or malformed
    - the scheme is not ``https``
    - the host is in the blocked-names list
    - the host is a private, loopback, link-local, or unspecified IP address
    """
    if not url:
        raise ValueError(f"{label} must not be empty.")
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise ValueError(f"{label} is not a valid URL.")

    if parsed.scheme != "https":
        raise ValueError(f"{label} must use HTTPS (got '{parsed.scheme}://').")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError(f"{label} has no hostname.")

    if host in _OIDC_BLOCKED_NAMES:
        raise ValueError(f"{label} hostname '{host}' is not permitted.")

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return  # Domain name — allowed; DNS resolution happens at runtime.

    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        raise ValueError(f"{label} must not target a private or reserved IP address.")


# ---------------------------------------------------------------------------
# OIDC state store — uses the app-level KV store (Redis or in-process dict)
# so that state tokens survive across replicas and process restarts.
# ---------------------------------------------------------------------------

async def store_state_async(kv_store, state: str, data: dict) -> None:
    """Persist OIDC anti-CSRF state in the KV store with a TTL of _STATE_TTL seconds."""
    await kv_store.set(f"oidc:state:{state}", {**data, "created_at": time.time()}, ttl=_STATE_TTL)


async def consume_state_async(kv_store, state: str) -> dict | None:
    """Atomically read-and-delete an OIDC state token from the KV store.

    Returns the stored data dict, or ``None`` if the token is missing or expired.
    """
    key = f"oidc:state:{state}"
    stored = await kv_store.get(key)
    if stored is None:
        return None
    await kv_store.delete(key)
    if time.time() - stored.get("created_at", 0) > _STATE_TTL:
        return None
    return stored


async def fetch_discovery_doc(issuer: str) -> dict:
    """Fetch the OpenID Connect discovery document from ``{issuer}/.well-known/openid-configuration``.

    Validates the *issuer* URL and all endpoint URLs returned by the document to
    prevent SSRF attacks via a malicious or misconfigured OIDC provider.
    """
    _validate_oidc_url(issuer, "issuer")
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    # follow_redirects=False prevents redirect-based SSRF to internal services.
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()

    # Validate every endpoint URL returned by the discovery document so a
    # compromised or attacker-controlled IdP cannot redirect token / userinfo
    # requests to internal services and exfiltrate bearer tokens.
    for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        ep = doc.get(field)
        if ep and isinstance(ep, str):
            try:
                _validate_oidc_url(ep, field)
            except ValueError as exc:
                raise ValueError(
                    f"OIDC discovery document contains an unsafe {field}: {exc}"
                ) from exc

    return doc


def build_auth_url(
    discovery_doc: dict,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    """Construct the IdP authorization URL using urllib.parse.urlencode (consistent with exchange_code)."""
    auth_endpoint = discovery_doc["authorization_endpoint"]
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
    })
    return f"{auth_endpoint}?{params}"


async def exchange_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    token_endpoint: str,
) -> dict:
    """Exchange authorization code for tokens at the IdP token endpoint.

    Uses httpx's built-in form-data encoding (data=) which is consistent with
    build_auth_url's use of urllib.parse.urlencode — both produce form-encoded
    key=value pairs, just for different HTTP verbs (POST body vs GET query string).
    """
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(access_token: str, userinfo_endpoint: str) -> dict:
    """Fetch user profile from the IdP userinfo endpoint."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()
