"""Tests for the SSO / OIDC login flow (app/api/routes/sso.py + app/services/oidc_service.py).

All external IdP HTTP calls are mocked so no live network is needed.
The app's in-process KV store (RedisStore(None) in-memory mode) is used for
state tokens, which means the full login → callback round-trip works through
the TestClient without Redis.

Fixtures: client, make_user from conftest.py.
"""
from __future__ import annotations

import urllib.parse
import uuid as _uuid
from unittest.mock import AsyncMock, patch

import pytest

_FAKE_ISSUER = "https://idp.example.com"
_FAKE_DISCOVERY = {
    "issuer": _FAKE_ISSUER,
    "authorization_endpoint": "https://idp.example.com/auth",
    "token_endpoint": "https://idp.example.com/token",
    "userinfo_endpoint": "https://idp.example.com/userinfo",
}
_FAKE_TOKENS = {"access_token": "fake-access-token", "token_type": "bearer"}
_FAKE_USERINFO = {"sub": "external-user-001", "email": "tester@example.com"}


def _create_provider(client, admin):
    """Create an OIDC provider in the workspace; returns provider JSON."""
    with patch("app.api.routes.sso.fetch_discovery_doc", AsyncMock(return_value=_FAKE_DISCOVERY)):
        resp = client.post(
            "/auth/sso/providers",
            json={
                "name": "Test IdP",
                "issuer": _FAKE_ISSUER,
                "clientId": "my-client-id",
                "clientSecret": "my-client-secret",
            },
            headers=admin.auth_headers,
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _initiate_login(client, provider_id):
    """Call the login endpoint (no redirect follow) and return the location URL."""
    with patch("app.api.routes.sso.fetch_discovery_doc", AsyncMock(return_value=_FAKE_DISCOVERY)):
        resp = client.get(f"/auth/sso/login/{provider_id}", follow_redirects=False)
    assert resp.status_code in (302, 307), resp.text
    return resp.headers["location"]


def _extract_state(location: str) -> str:
    parsed = urllib.parse.urlparse(location)
    qs = urllib.parse.parse_qs(parsed.query)
    states = qs.get("state", [])
    assert states, f"No state param in redirect URL: {location}"
    return states[0]


def _do_callback(client, state, userinfo=None, tokens=None):
    """Run the OIDC callback with mocked IdP responses."""
    ui = userinfo if userinfo is not None else _FAKE_USERINFO
    tok = tokens if tokens is not None else _FAKE_TOKENS
    with patch("app.api.routes.sso.fetch_discovery_doc", AsyncMock(return_value=_FAKE_DISCOVERY)), \
         patch("app.api.routes.sso.exchange_code", AsyncMock(return_value=tok)), \
         patch("app.api.routes.sso.fetch_userinfo", AsyncMock(return_value=ui)):
        return client.get(f"/auth/sso/callback?code=fake_code&state={state}")


# ── Provider management ───────────────────────────────────────────────────────

def test_create_provider_admin_only(client, make_user):
    """Non-admin cannot create an OIDC provider."""
    member = make_user(role="member")
    with patch("app.api.routes.sso.fetch_discovery_doc", AsyncMock(return_value=_FAKE_DISCOVERY)):
        resp = client.post(
            "/auth/sso/providers",
            json={"name": "X", "issuer": _FAKE_ISSUER, "clientId": "c", "clientSecret": "s"},
            headers=member.auth_headers,
        )
    assert resp.status_code == 403


def test_list_providers_workspace_scoped(client, make_user):
    """List only returns providers in the caller's workspace."""
    admin_a = make_user(role="admin")
    admin_b = make_user(role="admin")

    _create_provider(client, admin_a)

    resp_a = client.get("/auth/sso/providers", headers=admin_a.auth_headers)
    resp_b = client.get("/auth/sso/providers", headers=admin_b.auth_headers)

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["count"] == 1
    assert resp_b.json()["count"] == 0  # different workspace, sees nothing


# ── Full login → callback happy path ─────────────────────────────────────────

def test_valid_oidc_login_provisions_user_in_correct_workspace(client, make_user):
    """Valid OIDC callback provisions/links a user to the provider's workspace."""
    admin = make_user(role="admin")
    provider = _create_provider(client, admin)
    provider_id = provider["id"]

    location = _initiate_login(client, provider_id)
    state = _extract_state(location)

    resp = _do_callback(client, state)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert "accessToken" in data
    assert "refreshToken" in data
    assert data["workspaceId"] == admin.workspace_id
    assert data["user"]["workspaceId"] == admin.workspace_id
    assert data["user"]["username"] == "sso_external-user-001"


def test_second_login_links_existing_user(client, make_user):
    """A repeat OIDC login reuses the existing sso_ user rather than creating a duplicate."""
    admin = make_user(role="admin")
    provider = _create_provider(client, admin)
    provider_id = provider["id"]

    # First login
    state1 = _extract_state(_initiate_login(client, provider_id))
    r1 = _do_callback(client, state1)
    assert r1.status_code == 200
    user_id_1 = r1.json()["user"]["id"]

    # Second login
    state2 = _extract_state(_initiate_login(client, provider_id))
    r2 = _do_callback(client, state2)
    assert r2.status_code == 200
    user_id_2 = r2.json()["user"]["id"]

    # Same user object — no duplicate created
    assert user_id_1 == user_id_2


# ── Anti-CSRF / replay protection ────────────────────────────────────────────

def test_replayed_state_is_rejected(client, make_user):
    """A state token consumed on the first callback is invalid on replay."""
    admin = make_user(role="admin")
    provider = _create_provider(client, admin)
    provider_id = provider["id"]

    state = _extract_state(_initiate_login(client, provider_id))

    # First callback — succeeds, consumes state
    r1 = _do_callback(client, state)
    assert r1.status_code == 200

    # Second callback with same state — must be rejected
    r2 = _do_callback(client, state)
    assert r2.status_code == 400


def test_tampered_state_is_rejected(client, make_user):
    """A state value that was never stored in the KV is rejected."""
    make_user(role="admin")  # ensure setup guard is satisfied
    r = _do_callback(client, "definitely-not-a-real-state-token")
    assert r.status_code == 400


# ── Malformed / missing parameters ───────────────────────────────────────────

def test_callback_missing_code_returns_400(client, make_user):
    """Callback with no code returns 400, not 500."""
    make_user(role="admin")
    resp = client.get("/auth/sso/callback?state=somestate")
    assert resp.status_code == 400


def test_callback_missing_state_returns_400(client, make_user):
    """Callback with no state returns 400, not 500."""
    make_user(role="admin")
    resp = client.get("/auth/sso/callback?code=somecode")
    assert resp.status_code == 400


def test_callback_oidc_error_param_returns_400(client, make_user):
    """If the IdP sends an error query param the callback must return 400."""
    make_user(role="admin")
    resp = client.get("/auth/sso/callback?error=access_denied&error_description=User+denied")
    assert resp.status_code == 400


def test_callback_missing_sub_returns_error(client, make_user):
    """Userinfo with no 'sub' claim must not return 200 or 500 — must be an error response."""
    admin = make_user(role="admin")
    provider = _create_provider(client, admin)
    provider_id = provider["id"]

    state = _extract_state(_initiate_login(client, provider_id))
    # Userinfo with no sub (and no id)
    resp = _do_callback(client, state, userinfo={"email": "anon@example.com"})
    assert resp.status_code in (400, 502)


# ── Provider not found / disabled ────────────────────────────────────────────

def test_login_unknown_provider_returns_404(client, make_user):
    """Login for a provider UUID that doesn't exist returns 404."""
    make_user(role="admin")
    resp = client.get(f"/auth/sso/login/{_uuid.uuid4()}", follow_redirects=False)
    assert resp.status_code == 404


def test_login_invalid_uuid_returns_404(client, make_user):
    """Login with a non-UUID provider id returns 404."""
    make_user(role="admin")
    resp = client.get("/auth/sso/login/not-a-uuid", follow_redirects=False)
    assert resp.status_code == 404
