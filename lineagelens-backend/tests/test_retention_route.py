"""API contract tests for retention policies (/retention).

Covers: a policy can be created, it is workspace-scoped, and writes are admin-only.

Run with:
    cd lineagelens-backend && pytest tests/test_retention_route.py -q
"""
from __future__ import annotations


def test_admin_can_create_and_read_policy(client, make_user):
    admin = make_user(role="admin")

    created = client.put(
        "/retention",
        json={"retain_days": 30, "redact_after_days": 7, "enabled": True},
        headers=admin.auth_headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["workspace_id"] == admin.workspace_id
    assert body["retain_days"] == 30
    assert body["redact_after_days"] == 7
    assert body["enabled"] is True

    fetched = client.get("/retention", headers=admin.auth_headers).json()
    assert fetched["retain_days"] == 30
    assert fetched["enabled"] is True


def test_policy_is_workspace_scoped(client, make_user):
    admin_a = make_user(role="admin", workspace_id="ws-a", username="admin-a")
    admin_b = make_user(role="admin", workspace_id="ws-b", username="admin-b")

    set_a = client.put(
        "/retention",
        json={"retain_days": 30, "enabled": True},
        headers=admin_a.auth_headers,
    )
    assert set_a.status_code == 200, set_a.text

    # Workspace B never had a policy set -> it must see defaults, not A's values.
    b_view = client.get("/retention", headers=admin_b.auth_headers).json()
    assert b_view["workspace_id"] == "ws-b"
    assert b_view["retain_days"] == 365
    assert b_view["enabled"] is False


def test_non_admin_cannot_update_policy(client, make_user):
    member = make_user(role="member")
    resp = client.put(
        "/retention",
        json={"retain_days": 90, "enabled": True},
        headers=member.auth_headers,
    )
    assert resp.status_code == 403


def test_reviewer_cannot_update_policy(client, make_user):
    reviewer = make_user(role="reviewer")
    resp = client.put(
        "/retention",
        json={"retain_days": 90, "enabled": True},
        headers=reviewer.auth_headers,
    )
    assert resp.status_code == 403
