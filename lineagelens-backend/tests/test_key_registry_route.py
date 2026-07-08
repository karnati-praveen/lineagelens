"""Route-level tests for /admin/keys (PART 5 #57)."""
from __future__ import annotations


def test_non_admin_gets_403_on_list(client, make_user):
    member = make_user(role="member")
    resp = client.get("/admin/keys", headers=member.auth_headers)
    assert resp.status_code == 403


def test_admin_can_list_keys(client, make_user):
    admin = make_user(role="admin")
    resp = client.get("/admin/keys", headers=admin.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "keys" in body
    assert "currentActivePublicKeyId" in body


def test_admin_can_register_and_fetch_key(client, make_user):
    admin = make_user(role="admin")

    create = client.post(
        "/admin/keys",
        json={"publicKeyId": "route-test-key-1", "publicKeyHex": "ab" * 32, "label": "route test"},
        headers=admin.auth_headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["publicKeyId"] == "route-test-key-1"
    assert body["status"] == "active"

    get_resp = client.get("/admin/keys/route-test-key-1", headers=admin.auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["publicKeyId"] == "route-test-key-1"


def test_register_duplicate_key_returns_409(client, make_user):
    admin = make_user(role="admin")
    payload = {"publicKeyId": "dup-key", "publicKeyHex": "cd" * 32}
    first = client.post("/admin/keys", json=payload, headers=admin.auth_headers)
    assert first.status_code == 201

    second = client.post("/admin/keys", json=payload, headers=admin.auth_headers)
    assert second.status_code == 409


def test_get_unknown_key_returns_404(client, make_user):
    admin = make_user(role="admin")
    resp = client.get("/admin/keys/does-not-exist-anywhere", headers=admin.auth_headers)
    assert resp.status_code == 404


def test_revoke_key_updates_status_and_writes_audit(client, make_user, db_query):
    admin = make_user(role="admin")
    client.post(
        "/admin/keys",
        json={"publicKeyId": "revoke-route-key", "publicKeyHex": "ef" * 32},
        headers=admin.auth_headers,
    )

    revoke_resp = client.post(
        "/admin/keys/revoke-route-key/revoke",
        json={"reason": "suspected leak"},
        headers=admin.auth_headers,
    )
    assert revoke_resp.status_code == 200, revoke_resp.text
    body = revoke_resp.json()
    assert body["status"] == "compromised"
    assert body["revocationReason"] == "suspected leak"

    async def _fetch_audit(session):
        from sqlalchemy import select
        from app.db.models import AuditLog

        result = await session.execute(
            select(AuditLog.action).where(AuditLog.workspace_id == admin.workspace_id)
        )
        return list(result.scalars().all())

    actions = db_query(_fetch_audit)
    assert "key_registry.register" in actions
    assert "key_registry.revoke" in actions


def test_revoke_unknown_key_returns_404(client, make_user):
    admin = make_user(role="admin")
    resp = client.post(
        "/admin/keys/nonexistent-key-id/revoke",
        json={"reason": "test"},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 404


def test_non_admin_gets_403_on_revoke(client, make_user):
    member = make_user(role="member")
    resp = client.post(
        "/admin/keys/whatever/revoke", json={"reason": "test"}, headers=member.auth_headers
    )
    assert resp.status_code == 403
