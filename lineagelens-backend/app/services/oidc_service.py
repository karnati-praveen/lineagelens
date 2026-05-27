from __future__ import annotations

import logging
import time
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

# In-memory state store (anti-CSRF for OIDC flows)
# state_token -> {provider_id, workspace_id, created_at}
_state_store: dict[str, dict] = {}
_STATE_TTL = 300  # 5 minutes


def store_state(state: str, data: dict) -> None:
    _state_store[state] = {**data, "created_at": time.time()}
    _cleanup_states()


def consume_state(state: str) -> dict | None:
    _cleanup_states()
    return _state_store.pop(state, None)


def _cleanup_states() -> None:
    now = time.time()
    stale = [k for k, v in _state_store.items() if now - v.get("created_at", 0) > _STATE_TTL]
    for k in stale:
        del _state_store[k]


async def fetch_discovery_doc(issuer: str) -> dict:
    """Fetch OpenID Connect discovery document from {issuer}/.well-known/openid-configuration."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


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
    async with httpx.AsyncClient(timeout=10.0) as client:
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
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()
