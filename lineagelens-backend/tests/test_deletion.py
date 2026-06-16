"""Tests for app/api/routes/deletion.py.

Covers:
  - Hard-delete is workspace-scoped (cannot delete another workspace's record)
  - Deletion writes an audit event
  - Non-admin is forbidden from deleting
  - Soft-redact clears sensitive fields and marks is_redacted=True
  - Redacting an already-redacted record returns 409
  - Redact writes an audit event
  - Right-to-erasure (hard delete) removes the row from the DB

Fixtures: client, make_user, db_query from conftest.py.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, UTC

from sqlalchemy import select

from app.db.models import AuditLog, ProvenanceRecord


def _audit_actions(db_query, workspace_id: str) -> list[str]:
    async def _fetch(session):
        result = await session.execute(
            select(AuditLog.action).where(AuditLog.workspace_id == workspace_id)
        )
        return [row[0] for row in result.all()]
    return db_query(_fetch)


def _seed_record(db_query, workspace_id: str, *, inserted_code: str = "print('hello')") -> str:
    """Insert a ProvenanceRecord and return its UUID string."""
    async def _run(session):
        rec_uuid = _uuid.uuid4()
        rec = ProvenanceRecord(
            uuid=rec_uuid,
            workspace_id=workspace_id,
            file_path="test/file.py",
            timestamp_iso=datetime.now(UTC),
            inserted_code=inserted_code,
            provenance_payload={"source": "test"},
            prompt_messages=[{"role": "user", "content": "write code"}],
            raw_model_response="print('hello')",
            surrounding_context={"before": "", "after": ""},
        )
        session.add(rec)
        await session.commit()
        return str(rec_uuid)
    return db_query(_run)


def _record_exists(db_query, workspace_id: str, record_uuid: str) -> bool:
    async def _fetch(session):
        import uuid as _u
        result = await session.execute(
            select(ProvenanceRecord).where(
                ProvenanceRecord.uuid == _u.UUID(record_uuid),
                ProvenanceRecord.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none() is not None
    return db_query(_fetch)


def _get_record(db_query, workspace_id: str, record_uuid: str):
    async def _fetch(session):
        import uuid as _u
        result = await session.execute(
            select(ProvenanceRecord).where(
                ProvenanceRecord.uuid == _u.UUID(record_uuid),
                ProvenanceRecord.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()
    return db_query(_fetch)


# ── Hard delete ───────────────────────────────────────────────────────────────

def test_admin_can_delete_own_workspace_record(client, make_user, db_query):
    """Admin can hard-delete a record in their own workspace."""
    admin = make_user(role="admin")
    record_uuid = _seed_record(db_query, admin.workspace_id)

    resp = client.delete(f"/provenance/{record_uuid}", headers=admin.auth_headers)
    assert resp.status_code == 204, resp.text

    assert not _record_exists(db_query, admin.workspace_id, record_uuid)


def test_delete_writes_audit_event(client, make_user, db_query):
    """Deleting a record writes a record.delete audit log entry."""
    admin = make_user(role="admin")
    record_uuid = _seed_record(db_query, admin.workspace_id)

    client.delete(f"/provenance/{record_uuid}", headers=admin.auth_headers)

    assert "record.delete" in _audit_actions(db_query, admin.workspace_id)


def test_non_admin_cannot_delete(client, make_user, db_query):
    """Non-admin (member) cannot delete a provenance record — must get 403."""
    member = make_user(role="member")
    record_uuid = _seed_record(db_query, member.workspace_id)

    resp = client.delete(f"/provenance/{record_uuid}", headers=member.auth_headers)
    assert resp.status_code == 403

    # Record still exists
    assert _record_exists(db_query, member.workspace_id, record_uuid)


def test_delete_cross_workspace_returns_404(client, make_user, db_query):
    """Admin cannot delete a record from a different workspace."""
    owner = make_user(role="admin")
    attacker = make_user(role="admin")  # different workspace

    assert owner.workspace_id != attacker.workspace_id

    record_uuid = _seed_record(db_query, owner.workspace_id)

    # Attacker calls delete with the owner's record UUID
    resp = client.delete(f"/provenance/{record_uuid}", headers=attacker.auth_headers)
    assert resp.status_code == 404

    # Record untouched in owner's workspace
    assert _record_exists(db_query, owner.workspace_id, record_uuid)


def test_delete_nonexistent_record_returns_404(client, make_user):
    """Deleting a record UUID that doesn't exist returns 404."""
    admin = make_user(role="admin")
    resp = client.delete(f"/provenance/{_uuid.uuid4()}", headers=admin.auth_headers)
    assert resp.status_code == 404


def test_delete_unauthenticated_returns_401(client, make_user, db_query):
    """Unauthenticated delete request returns 401."""
    admin = make_user(role="admin")
    record_uuid = _seed_record(db_query, admin.workspace_id)
    resp = client.delete(f"/provenance/{record_uuid}")
    assert resp.status_code == 401


# ── Soft redact (right-to-erasure path) ──────────────────────────────────────

def test_admin_can_redact_record(client, make_user, db_query):
    """Admin can redact a record; sensitive fields are cleared and is_redacted is True."""
    admin = make_user(role="admin")
    record_uuid = _seed_record(db_query, admin.workspace_id)

    resp = client.patch(f"/provenance/{record_uuid}/redact", headers=admin.auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["is_redacted"] is True

    # Verify at DB level that sensitive fields are cleared
    rec = _get_record(db_query, admin.workspace_id, record_uuid)
    assert rec is not None
    assert rec.is_redacted is True
    assert rec.prompt_messages is None
    assert rec.raw_model_response is None
    assert rec.surrounding_context is None
    assert rec.context_snapshot is None


def test_redact_writes_audit_event(client, make_user, db_query):
    """Redacting a record writes a record.redact audit log entry."""
    admin = make_user(role="admin")
    record_uuid = _seed_record(db_query, admin.workspace_id)

    client.patch(f"/provenance/{record_uuid}/redact", headers=admin.auth_headers)

    assert "record.redact" in _audit_actions(db_query, admin.workspace_id)


def test_redact_already_redacted_returns_409(client, make_user, db_query):
    """Redacting an already-redacted record returns 409 Conflict."""
    admin = make_user(role="admin")
    record_uuid = _seed_record(db_query, admin.workspace_id)

    r1 = client.patch(f"/provenance/{record_uuid}/redact", headers=admin.auth_headers)
    assert r1.status_code == 200

    r2 = client.patch(f"/provenance/{record_uuid}/redact", headers=admin.auth_headers)
    assert r2.status_code == 409


def test_redact_cross_workspace_returns_404(client, make_user, db_query):
    """Admin cannot redact a record from a different workspace."""
    owner = make_user(role="admin")
    attacker = make_user(role="admin")

    assert owner.workspace_id != attacker.workspace_id
    record_uuid = _seed_record(db_query, owner.workspace_id)

    resp = client.patch(f"/provenance/{record_uuid}/redact", headers=attacker.auth_headers)
    assert resp.status_code == 404

    # Record is not redacted in owner's workspace
    rec = _get_record(db_query, owner.workspace_id, record_uuid)
    assert rec is not None
    assert rec.is_redacted is False


def test_non_admin_cannot_redact(client, make_user, db_query):
    """Non-admin cannot redact a record."""
    member = make_user(role="member")
    record_uuid = _seed_record(db_query, member.workspace_id)

    resp = client.patch(f"/provenance/{record_uuid}/redact", headers=member.auth_headers)
    assert resp.status_code == 403
