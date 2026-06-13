"""API contract tests for the API-key lifecycle (/api-keys) and key auth.

Covers create/list/revoke, that a revoked key stops authenticating (exercised
against the one endpoint that accepts API keys, POST /github/check), and the
actual authorization model of the route.

Run with:
    cd lineagelens-backend && pytest tests/test_api_keys_route.py -q
"""
from __future__ import annotations


def _create_key(client, user, name="ci-key", scopes=None):
    body = {"name": name, "scopes": scopes or ["read"]}
    return client.post("/api-keys", json=body, headers=user.auth_headers)


def test_api_key_create_list_revoke_lifecycle(client, make_user):
    user = make_user(role="member")

    created = _create_key(client, user, name="build-bot")
    assert created.status_code == 201, created.text
    payload = created.json()
    key_id = payload["id"]
    # The full secret is returned exactly once, on creation.
    assert payload["key"].startswith("llk_")
    assert payload["isActive"] is True

    listed = client.get("/api-keys", headers=user.auth_headers)
    assert listed.status_code == 200
    results = listed.json()["results"]
    assert any(k["id"] == key_id for k in results)
    # The raw secret must never appear in the list view.
    assert all("key" not in k for k in results)

    revoked = client.delete(f"/api-keys/{key_id}", headers=user.auth_headers)
    assert revoked.status_code == 204

    after = client.get("/api-keys", headers=user.auth_headers).json()["results"]
    revoked_row = next(k for k in after if k["id"] == key_id)
    assert revoked_row["isActive"] is False


def test_revoked_key_stops_authenticating(client, make_user):
    user = make_user(role="member")
    created = _create_key(client, user, name="cicd")
    full_key = created.json()["key"]
    key_id = created.json()["id"]

    check_body = {"filePath": "app/x.py", "code": "print('hi')"}

    # Active key authenticates against the CI gate endpoint.
    ok = client.post("/github/check", json=check_body, headers={"X-API-Key": full_key})
    assert ok.status_code == 200, ok.text

    assert client.delete(f"/api-keys/{key_id}", headers=user.auth_headers).status_code == 204

    # Same key, now revoked -> 401.
    denied = client.post("/github/check", json=check_body, headers={"X-API-Key": full_key})
    assert denied.status_code == 401


def test_github_check_rejects_unknown_key(client, make_user):
    make_user(role="member")  # ensure setup complete
    denied = client.post(
        "/github/check",
        json={"filePath": "a.py", "code": "x = 1"},
        headers={"X-API-Key": "llk_does-not-exist"},
    )
    assert denied.status_code == 401


def test_api_keys_are_isolated_per_owner(client, make_user):
    workspace = "shared-ws"
    alice = make_user(role="member", workspace_id=workspace, username="alice")
    bob = make_user(role="member", workspace_id=workspace, username="bob")

    alice_key_id = _create_key(client, alice, name="alice-key").json()["id"]

    bob_list = client.get("/api-keys", headers=bob.auth_headers).json()["results"]
    assert all(k["id"] != alice_key_id for k in bob_list)


def test_api_key_management_is_not_role_gated(client, make_user):
    """Contract observation, not a bug.

    The task brief assumed member-vs-admin RBAC on /api-keys, but the route uses
    get_current_auth_context with no role check — keys are per-user, available to
    any authenticated role. This test pins the *actual* behavior so a future
    intentional change is caught.
    """
    member = make_user(role="member")
    admin = make_user(role="admin")

    assert _create_key(client, member, name="m").status_code == 201
    assert _create_key(client, admin, name="a").status_code == 201


def test_api_key_rejects_invalid_scopes(client, make_user):
    user = make_user(role="member")
    resp = client.post(
        "/api-keys",
        json={"name": "bad", "scopes": ["read", "superuser"]},
        headers=user.auth_headers,
    )
    assert resp.status_code == 400
