"""Integration tests for POST /auth/change-password.

Exercises the full register → change-password → login flow against the
isolated TestClient/SQLite fixture so we cover the real route, not helpers.

Run with:
    cd lineagelens-backend && pytest tests/test_auth_change_password.py -v
"""
from __future__ import annotations

import uuid as _uuid

OLD_PW = "OldPass1!"
NEW_PW = "NewPass2@"


def _register(client) -> tuple[str, str, str]:
    """Register a fresh admin and return (username, workspace_id, access_token)."""
    username = f"pw-user-{_uuid.uuid4().hex[:8]}"
    workspace = f"ws-{_uuid.uuid4().hex[:8]}"
    res = client.post(
        "/auth/register",
        json={"username": username, "password": OLD_PW, "workspaceId": workspace},
    )
    assert res.status_code == 201, res.text
    return username, workspace, res.json()["accessToken"]


def test_change_password_happy_path_and_relogin(client) -> None:
    username, _, token = _register(client)

    res = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": OLD_PW, "newPassword": NEW_PW},
    )
    assert res.status_code == 200, res.text
    assert res.json()["accessToken"]  # fresh tokens returned

    # New password works…
    ok = client.post("/auth/login", json={"username": username, "password": NEW_PW})
    assert ok.status_code == 200, ok.text
    # …and the old one no longer does.
    bad = client.post("/auth/login", json={"username": username, "password": OLD_PW})
    assert bad.status_code == 401


def test_change_password_rejects_wrong_current(client) -> None:
    _, _, token = _register(client)
    res = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": "WrongPass9!", "newPassword": NEW_PW},
    )
    assert res.status_code == 401


def test_change_password_enforces_strength(client) -> None:
    _, _, token = _register(client)
    res = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": OLD_PW, "newPassword": "weak"},
    )
    assert res.status_code == 400


def test_change_password_rejects_same_password(client) -> None:
    _, _, token = _register(client)
    res = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"currentPassword": OLD_PW, "newPassword": OLD_PW},
    )
    assert res.status_code == 400


def test_change_password_requires_auth(client) -> None:
    res = client.post(
        "/auth/change-password",
        json={"currentPassword": OLD_PW, "newPassword": NEW_PW},
    )
    assert res.status_code == 401
