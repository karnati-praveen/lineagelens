"""Tests for app/api/routes/permissions.py and app.core.security.build_record_visibility_clause.

Covers:
  - Grant / revoke ResourcePermission via HTTP API
  - RBAC enforcement (admin-only)
  - Workspace isolation (ensure_workspace_scope)
  - build_record_visibility_clause logic: no-row = open, row-for-other-user = hidden,
    admin override = sees all

Fixtures: client, make_user, db_query from conftest.py.
"""
from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select

from app.db.models import AuditLog, ResourcePermission


def _audit_actions(db_query, workspace_id: str) -> list[str]:
    async def _fetch(session):
        result = await session.execute(
            select(AuditLog.action).where(AuditLog.workspace_id == workspace_id)
        )
        return [row[0] for row in result.all()]
    return db_query(_fetch)


def _all_perms(db_query, workspace_id: str):
    async def _fetch(session):
        result = await session.execute(
            select(ResourcePermission).where(ResourcePermission.workspace_id == workspace_id)
        )
        return list(result.scalars().all())
    return db_query(_fetch)


def _seed_permission(db_query, workspace_id: str, record_uuid: str, user_id: str, can_view: bool = True):
    async def _run(session):
        perm = ResourcePermission(
            workspace_id=workspace_id,
            record_uuid=record_uuid,
            user_id=user_id,
            can_view=can_view,
            can_edit=False,
            can_delete=False,
        )
        session.add(perm)
        await session.commit()
    db_query(_run)


# ── Grant ─────────────────────────────────────────────────────────────────────

def test_admin_can_grant_permission(client, make_user, db_query):
    """Admin can create a ResourcePermission and it is persisted."""
    admin = make_user(role="admin")
    target_user = make_user(role="member", workspace_id=admin.workspace_id)
    record_uuid = str(_uuid.uuid4())

    resp = client.post(
        "/permissions",
        json={
            "workspaceId": admin.workspace_id,
            "recordUuid": record_uuid,
            "userId": target_user.id,
            "canView": True,
            "canEdit": False,
            "canDelete": False,
        },
        headers=admin.auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["recordUuid"] == record_uuid
    assert data["userId"] == target_user.id
    assert data["canView"] is True
    assert data["workspaceId"] == admin.workspace_id

    # Persisted in DB
    perms = _all_perms(db_query, admin.workspace_id)
    assert any(p.record_uuid == record_uuid and p.user_id == target_user.id for p in perms)


def test_member_cannot_grant_permission(client, make_user):
    """Non-admin cannot call POST /permissions."""
    member = make_user(role="member")
    resp = client.post(
        "/permissions",
        json={
            "workspaceId": member.workspace_id,
            "recordUuid": str(_uuid.uuid4()),
            "userId": member.id,
        },
        headers=member.auth_headers,
    )
    assert resp.status_code == 403


def test_grant_cross_workspace_denied(client, make_user):
    """Admin cannot grant a permission for a different workspace (ensure_workspace_scope)."""
    admin = make_user(role="admin")
    other_ws = f"other-ws-{_uuid.uuid4().hex[:8]}"

    resp = client.post(
        "/permissions",
        json={
            "workspaceId": other_ws,  # mismatch with token workspace
            "recordUuid": str(_uuid.uuid4()),
            "userId": admin.id,
            "canView": True,
        },
        headers=admin.auth_headers,
    )
    assert resp.status_code == 403


def test_grant_is_idempotent_upsert(client, make_user, db_query):
    """Granting the same record+user twice updates in place — no duplicate rows."""
    admin = make_user(role="admin")
    record_uuid = str(_uuid.uuid4())
    user_id = admin.id
    ws = admin.workspace_id

    payload = {
        "workspaceId": ws,
        "recordUuid": record_uuid,
        "userId": user_id,
        "canView": True,
        "canEdit": False,
    }
    r1 = client.post("/permissions", json=payload, headers=admin.auth_headers)
    assert r1.status_code == 201  # created

    payload["canEdit"] = True
    r2 = client.post("/permissions", json=payload, headers=admin.auth_headers)
    # Route decorator always returns 201 regardless of create-vs-update
    assert r2.status_code == 201
    assert r2.json()["canEdit"] is True  # value was updated

    # Exactly one permission row exists for this record+user
    perms = _all_perms(db_query, ws)
    matching = [p for p in perms if p.record_uuid == record_uuid and p.user_id == user_id]
    assert len(matching) == 1
    assert matching[0].can_edit is True


def test_get_record_permissions_admin_only(client, make_user):
    """Non-admin cannot list permissions for a record."""
    member = make_user(role="member")
    resp = client.get(f"/permissions/record/{_uuid.uuid4()}", headers=member.auth_headers)
    assert resp.status_code == 403


# ── Revoke ────────────────────────────────────────────────────────────────────

def test_admin_can_revoke_permission(client, make_user, db_query):
    """Admin can DELETE a permission and it is removed from the DB."""
    admin = make_user(role="admin")
    record_uuid = str(_uuid.uuid4())

    created = client.post(
        "/permissions",
        json={
            "workspaceId": admin.workspace_id,
            "recordUuid": record_uuid,
            "userId": admin.id,
            "canView": True,
        },
        headers=admin.auth_headers,
    )
    assert created.status_code == 201
    perm_id = created.json()["id"]

    revoke_resp = client.delete(f"/permissions/{perm_id}", headers=admin.auth_headers)
    assert revoke_resp.status_code == 204

    perms = _all_perms(db_query, admin.workspace_id)
    assert not any(p.record_uuid == record_uuid for p in perms)


def test_revoke_writes_audit_event(client, make_user, db_query):
    """Revoking a permission must write a permission.revoke audit log entry."""
    admin = make_user(role="admin")
    record_uuid = str(_uuid.uuid4())

    created = client.post(
        "/permissions",
        json={
            "workspaceId": admin.workspace_id,
            "recordUuid": record_uuid,
            "userId": admin.id,
            "canView": True,
        },
        headers=admin.auth_headers,
    )
    perm_id = created.json()["id"]
    client.delete(f"/permissions/{perm_id}", headers=admin.auth_headers)

    assert "permission.revoke" in _audit_actions(db_query, admin.workspace_id)


def test_revoke_cross_workspace_returns_404(client, make_user):
    """Admin A cannot revoke admin B's permission (workspace-scoped DELETE)."""
    admin_a = make_user(role="admin")
    admin_b = make_user(role="admin")

    created = client.post(
        "/permissions",
        json={
            "workspaceId": admin_a.workspace_id,
            "recordUuid": str(_uuid.uuid4()),
            "userId": admin_a.id,
            "canView": True,
        },
        headers=admin_a.auth_headers,
    )
    perm_id = created.json()["id"]

    resp = client.delete(f"/permissions/{perm_id}", headers=admin_b.auth_headers)
    assert resp.status_code == 404


# ── build_record_visibility_clause unit tests ─────────────────────────────────

def _is_visible(db_query, workspace_id: str, record_uuid: str, user_id: str, is_admin: bool) -> bool:
    """Run build_record_visibility_clause against the test DB and return True if visible."""
    async def _run(session):
        from sqlalchemy import literal
        from app.core.security import build_record_visibility_clause

        clause = build_record_visibility_clause(
            literal(record_uuid),
            workspace_id=workspace_id,
            user_id=user_id,
            is_admin=is_admin,
        )
        result = await session.execute(select(literal(1)).where(clause))
        return result.one_or_none() is not None

    return db_query(_run)


def test_visibility_no_rows_open_to_member(client, make_user, db_query):
    """A record with no permission rows is visible to any workspace member."""
    user = make_user(role="member")
    record_uuid = str(_uuid.uuid4())
    # No permission rows — should be visible
    assert _is_visible(db_query, user.workspace_id, record_uuid, user.id, is_admin=False)


def test_visibility_row_for_correct_user_shows_record(client, make_user, db_query):
    """A record with a can_view permission row for this user is visible to them."""
    user = make_user(role="member")
    record_uuid = str(_uuid.uuid4())
    _seed_permission(db_query, user.workspace_id, record_uuid, user.id, can_view=True)
    assert _is_visible(db_query, user.workspace_id, record_uuid, user.id, is_admin=False)


def test_visibility_row_for_other_user_hides_record(client, make_user, db_query):
    """A record restricted to user B is hidden from user A (existence of rows triggers ACL)."""
    user_a = make_user(role="member")
    user_b = make_user(role="member", workspace_id=user_a.workspace_id)
    record_uuid = str(_uuid.uuid4())
    # Only B has a permission row → A cannot see it
    _seed_permission(db_query, user_a.workspace_id, record_uuid, user_b.id, can_view=True)
    assert not _is_visible(db_query, user_a.workspace_id, record_uuid, user_a.id, is_admin=False)


def test_visibility_admin_override_sees_all(client, make_user, db_query):
    """is_admin=True bypasses all permission checks — everything is visible."""
    admin = make_user(role="admin")
    other_user = make_user(role="member", workspace_id=admin.workspace_id)
    record_uuid = str(_uuid.uuid4())
    # Restricted to other_user — but admin sees it anyway
    _seed_permission(db_query, admin.workspace_id, record_uuid, other_user.id, can_view=True)
    assert _is_visible(db_query, admin.workspace_id, record_uuid, admin.id, is_admin=True)
