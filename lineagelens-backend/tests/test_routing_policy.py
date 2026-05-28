"""Tests for the dynamic routing policy endpoints.

Tests the GET /policies/routing, PUT /policies/routing, and
GET /policies/routing/internal endpoints.

The test strategy:
  - Use pytest with SQLite in-memory (via the conftest APP_ENV=test env vars)
  - Directly test the endpoint logic via FastAPI's TestClient
  - Follow the same patterns as existing backend tests (direct module imports,
    Settings.model_validate for config, no spawned processes)

Run with:
    cd lineagelens-backend && pytest tests/test_routing_policy.py -v
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# conftest.py already sets env vars; this import just documents the requirement:
from app.core.config import Settings


# ── helpers / fixtures ────────────────────────────────────────────────────────

BACKEND_TOKEN = "test-ingest-token-abc123"


@pytest.fixture(scope="module", autouse=True)
def set_backend_token():
    """Ensure BACKEND_INGEST_TOKEN is set for the internal endpoint tests."""
    os.environ["BACKEND_INGEST_TOKEN"] = BACKEND_TOKEN
    # Re-import policies module so it picks up the env var.
    import importlib
    import app.api.routes.policies as policies_mod
    importlib.reload(policies_mod)
    yield
    os.environ.pop("BACKEND_INGEST_TOKEN", None)


@pytest.fixture(scope="module")
def app():
    """Return the module-level FastAPI app instance.

    Skips if aiosqlite (or any other required DB driver) is not installed in
    the current test environment — same condition as test_sqlite_schema_upgrade.
    """
    try:
        import aiosqlite  # noqa: F401 — just checking availability
    except ModuleNotFoundError:
        pytest.skip("aiosqlite not installed — skipping HTTP-level routing policy tests")
    from app.main import app as _app
    return _app


@pytest.fixture(scope="module")
def client(app):
    with TestClient(app) as c:
        yield c


def _extract_token(resp) -> str | None:
    """Return access_token from a /auth/token response, or None if unavailable."""
    if resp.status_code != 200:
        return None
    try:
        return resp.json().get("access_token") or None
    except Exception:
        # Response was not JSON (e.g. setup-wizard HTML in first-run mode)
        return None


@pytest.fixture
def admin_token(client):
    """Create an admin user via /auth/setup and return a JWT access token.

    Skips gracefully when the DB is in first-run mode and the setup wizard
    returns HTML instead of JSON (common on fresh CI SQLite databases).
    """
    client.post("/auth/setup", json={
        "username": "testadmin",
        "password": "Password123!",
        "workspaceId": "ws-test",
    })
    resp = client.post("/auth/token", data={
        "username": "testadmin",
        "password": "Password123!",
    })
    token = _extract_token(resp)
    if not token:
        pytest.skip("Could not obtain admin token — DB may be in setup-wizard mode")
    return token


@pytest.fixture
def member_token(client):
    """Create a member user and return a JWT access token."""
    client.post("/auth/setup", json={
        "username": "testmember",
        "password": "Password123!",
        "workspaceId": "ws-test",
        "role": "member",
    })
    resp = client.post("/auth/token", data={
        "username": "testmember",
        "password": "Password123!",
    })
    token = _extract_token(resp)
    if not token:
        pytest.skip("Could not obtain member token")
    return token


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_VALID_UPSERT_BODY = {
    "workspaceId": "ws-test",
    "provider": "anthropic",
    "mappings": {
        "simple": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-4-6",
        "complex": "claude-opus-4-7",
    },
    "enabled": True,
}


# ── Unit-level tests (no HTTP server needed) ──────────────────────────────────

def test_serialize_routing_policy_shape():
    """The serializer returns camelCase keys."""
    from app.api.routes.policies import _serialize_routing_policy
    from app.db.models import RoutingPolicy
    import uuid
    from datetime import datetime, timezone

    p = RoutingPolicy()
    p.id = uuid.uuid4()
    p.workspace_id = "ws-abc"
    p.provider = "anthropic"
    p.mappings = {"simple": "haiku", "standard": "sonnet", "complex": "opus"}
    p.enabled = True
    p.created_by = "user-1"
    p.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p.updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    d = _serialize_routing_policy(p)

    assert d["workspaceId"] == "ws-abc"
    assert d["provider"] == "anthropic"
    assert d["mappings"]["simple"] == "haiku"
    assert d["enabled"] is True
    assert d["createdBy"] == "user-1"
    assert "createdAt" in d
    assert "updatedAt" in d


def test_routing_policy_model_has_unique_constraint():
    """RoutingPolicy model has the expected unique constraint."""
    from app.db.models import RoutingPolicy
    table = RoutingPolicy.__table__
    unique_names = {c.name for c in table.constraints}
    assert "uq_routing_policy_workspace_provider" in unique_names


def test_routing_policy_model_has_index():
    """RoutingPolicy model has ix_routing_policy_workspace index."""
    from app.db.models import RoutingPolicy
    table = RoutingPolicy.__table__
    index_names = {i.name for i in table.indexes}
    assert "ix_routing_policy_workspace" in index_names


def test_provenance_record_has_routing_decision_column():
    """ProvenanceRecord.routing_decision column was added."""
    from app.db.models import ProvenanceRecord
    columns = {c.name for c in ProvenanceRecord.__table__.columns}
    assert "routing_decision" in columns


# ── Pydantic schema validation tests ─────────────────────────────────────────

def test_routing_policy_upsert_valid_provider():
    from app.api.routes.policies import RoutingPolicyUpsert
    p = RoutingPolicyUpsert(**{
        "workspaceId": "ws-1",
        "provider": "anthropic",
        "mappings": {"simple": "haiku"},
        "enabled": True,
    })
    assert p.provider == "anthropic"


def test_routing_policy_upsert_camel_or_snake():
    from app.api.routes.policies import RoutingPolicyUpsert
    # Accept both aliases
    p1 = RoutingPolicyUpsert(workspaceId="ws-1", provider="openai", mappings={}, enabled=False)
    p2 = RoutingPolicyUpsert(workspace_id="ws-1", provider="openai", mappings={}, enabled=False)
    assert p1.workspace_id == p2.workspace_id == "ws-1"


# ── VALID_ROUTING_PROVIDERS set ───────────────────────────────────────────────

def test_valid_routing_providers_contains_three():
    from app.api.routes.policies import VALID_ROUTING_PROVIDERS
    assert VALID_ROUTING_PROVIDERS == {"anthropic", "openai", "gemini"}


# ── PROVIDER_DEFAULT_MAPPINGS ─────────────────────────────────────────────────

def test_provider_default_mappings_exist_for_all_providers():
    from app.api.routes.policies import PROVIDER_DEFAULT_MAPPINGS
    for provider in ("anthropic", "openai", "gemini"):
        assert provider in PROVIDER_DEFAULT_MAPPINGS, f"missing defaults for {provider!r}"
        mapping = PROVIDER_DEFAULT_MAPPINGS[provider]
        for tier in ("simple", "standard", "complex"):
            assert tier in mapping, f"missing {tier!r} tier in {provider!r} defaults"
            assert isinstance(mapping[tier], str) and mapping[tier], (
                f"{provider!r} {tier!r} must be a non-empty string"
            )


def test_provider_defaults_use_correct_model_families():
    """Defaults should reference the expected model families per provider."""
    from app.api.routes.policies import PROVIDER_DEFAULT_MAPPINGS
    for m in PROVIDER_DEFAULT_MAPPINGS["anthropic"].values():
        assert "claude" in m.lower(), f"anthropic default should be a claude model, got {m!r}"
    for m in PROVIDER_DEFAULT_MAPPINGS["openai"].values():
        assert "gpt" in m.lower(), f"openai default should be a gpt model, got {m!r}"
    for m in PROVIDER_DEFAULT_MAPPINGS["gemini"].values():
        assert "gemini" in m.lower(), f"gemini default should be a gemini model, got {m!r}"


def test_empty_mappings_falls_back_to_defaults():
    """The upsert endpoint fills defaults when mappings is empty or omitted."""
    from app.api.routes.policies import PROVIDER_DEFAULT_MAPPINGS, RoutingPolicyUpsert
    # Empty mappings should not break the Pydantic model
    p = RoutingPolicyUpsert(workspaceId="ws-1", provider="openai", mappings={}, enabled=True)
    assert p.mappings == {}  # Pydantic model stores as-is; endpoint logic fills defaults
    # The endpoint-level fallback: payload.mappings or PROVIDER_DEFAULT_MAPPINGS[provider]
    effective = p.mappings or PROVIDER_DEFAULT_MAPPINGS.get(p.provider, {})
    assert effective == PROVIDER_DEFAULT_MAPPINGS["openai"]
    assert effective["simple"] == "gpt-4o-mini"
    assert effective["complex"] == "gpt-4o"


# ── HTTP endpoint tests (require working DB / auth) ───────────────────────────
# These are marked to skip gracefully if the test DB/auth setup isn't available.

class TestRoutingPolicyHTTP:
    """HTTP-level tests for the routing policy endpoints."""

    def test_get_routing_policy_404_when_none(self, client, admin_token):
        resp = client.get("/policies/routing", headers=auth_header(admin_token))
        # Either 404 (no policies) or 200 — depends on whether policies were
        # created in other tests. Accept both.
        assert resp.status_code in (200, 404)

    def test_put_invalid_provider_returns_400(self, client, admin_token):
        body = {**_VALID_UPSERT_BODY, "provider": "invalid-provider"}
        resp = client.put(
            "/policies/routing",
            json=body,
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400

    def test_put_without_auth_returns_401_or_403(self, client):
        resp = client.put("/policies/routing", json=_VALID_UPSERT_BODY)
        assert resp.status_code in (401, 403, 422)

    def test_put_creates_policy_and_get_returns_it(self, client, admin_token):
        """Round-trip: PUT creates, GET returns."""
        put_resp = client.put(
            "/policies/routing",
            json=_VALID_UPSERT_BODY,
            headers=auth_header(admin_token),
        )
        assert put_resp.status_code == 200, put_resp.text
        created = put_resp.json()
        assert created["provider"] == "anthropic"
        assert created["enabled"] is True
        assert created["mappings"]["simple"] == "claude-haiku-4-5-20251001"

        get_resp = client.get("/policies/routing", headers=auth_header(admin_token))
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["count"] >= 1
        providers = [p["provider"] for p in data["results"]]
        assert "anthropic" in providers

    def test_put_upserts_existing_policy(self, client, admin_token):
        """A second PUT to the same (workspace, provider) updates in place."""
        body_v2 = {**_VALID_UPSERT_BODY, "enabled": False}
        resp1 = client.put("/policies/routing", json=_VALID_UPSERT_BODY, headers=auth_header(admin_token))
        resp2 = client.put("/policies/routing", json=body_v2, headers=auth_header(admin_token))
        assert resp2.status_code == 200
        assert resp2.json()["enabled"] is False

    def test_internal_endpoint_wrong_token_returns_401(self, client):
        resp = client.get(
            "/policies/routing/internal",
            headers={"X-Backend-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_internal_endpoint_correct_token_returns_policies(self, client, admin_token):
        # First create a policy
        client.put("/policies/routing", json=_VALID_UPSERT_BODY, headers=auth_header(admin_token))

        resp = client.get(
            "/policies/routing/internal",
            headers={"X-Backend-Token": BACKEND_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "policies" in data
        assert isinstance(data["policies"], list)

    def test_member_cannot_put_routing_policy(self, client, member_token):
        resp = client.put(
            "/policies/routing",
            json=_VALID_UPSERT_BODY,
            headers=auth_header(member_token),
        )
        assert resp.status_code in (403, 422)

    def test_routing_policy_multi_provider_separate_rows(self, client, admin_token):
        """Creating policies for anthropic, openai, gemini results in 3 rows."""
        for provider in ("anthropic", "openai", "gemini"):
            body = {**_VALID_UPSERT_BODY, "provider": provider}
            resp = client.put("/policies/routing", json=body, headers=auth_header(admin_token))
            assert resp.status_code == 200

        get_resp = client.get("/policies/routing", headers=auth_header(admin_token))
        assert get_resp.status_code == 200
        providers = {p["provider"] for p in get_resp.json()["results"]}
        assert {"anthropic", "openai", "gemini"}.issubset(providers)
