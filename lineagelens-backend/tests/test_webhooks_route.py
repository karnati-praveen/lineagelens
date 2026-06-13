"""API contract tests for webhooks (/webhooks).

Covers: an admin can register a webhook, and the secret is never echoed back
(neither on registration nor on GET). Also pins the admin-only gate on register.

A public IP literal is used for the target URL so the SSRF guard's validation
path is exercised without depending on live DNS resolution.

Run with:
    cd lineagelens-backend && pytest tests/test_webhooks_route.py -q
"""
from __future__ import annotations

_PUBLIC_URL = "https://8.8.8.8/lineagelens-hook"
_SECRET = "whsec_super_secret_value"


def test_register_webhook_does_not_echo_secret(client, make_user):
    admin = make_user(role="admin")

    resp = client.post(
        "/webhooks",
        json={"url": _PUBLIC_URL, "secret": _SECRET, "risk_threshold": 80},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["url"] == _PUBLIC_URL
    # WebhookConfigPublic declares no field aliases -> JSON keys are snake_case.
    assert body["risk_threshold"] == 80
    assert "secret" not in body
    assert _SECRET not in resp.text


def test_get_webhooks_never_returns_secret(client, make_user):
    admin = make_user(role="admin")
    client.post(
        "/webhooks",
        json={"url": _PUBLIC_URL, "secret": _SECRET},
        headers=admin.auth_headers,
    )

    listed = client.get("/webhooks", headers=admin.auth_headers)
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert all("secret" not in item for item in items)
    assert _SECRET not in listed.text


def test_register_webhook_is_admin_only(client, make_user):
    member = make_user(role="member")
    resp = client.post(
        "/webhooks",
        json={"url": _PUBLIC_URL, "secret": _SECRET},
        headers=member.auth_headers,
    )
    assert resp.status_code == 403


def test_register_webhook_rejects_internal_url(client, make_user):
    admin = make_user(role="admin")
    resp = client.post(
        "/webhooks",
        json={"url": "http://localhost/hook", "secret": _SECRET},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 422
