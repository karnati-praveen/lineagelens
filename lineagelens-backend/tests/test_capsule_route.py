"""Route-level tests for /capsules (PART 5 #51)."""
from __future__ import annotations

import io
import uuid as _uuid
import zipfile
from datetime import UTC, datetime

from app.db.models import ProvenanceRecord


def _seed_provenance(db_query, workspace_id: str) -> str:
    async def _run(session):
        rid = _uuid.uuid4()
        rec = ProvenanceRecord(
            uuid=rid,
            workspace_id=workspace_id,
            file_path="f.py",
            timestamp_iso=datetime.now(UTC),
            inserted_code="x = 1",
            provenance_payload={},
            model_name="gpt-4o",
        )
        session.add(rec)
        await session.commit()
        return str(rid)

    return db_query(_run)


def test_member_can_build_own_workspace_capsule(client, make_user, tmp_path, monkeypatch):
    """Capsule export is available to any workspace member (like /integrity/verify),
    not admin-restricted — only cross-workspace access is blocked."""
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    member = make_user(role="member")
    resp = client.post("/capsules", params={"workspace_id": member.workspace_id}, headers=member.auth_headers)
    assert resp.status_code == 200, resp.text


def test_cannot_build_capsule_for_other_workspace(client, make_user):
    member_a = make_user(role="member")
    member_b = make_user(role="member")
    resp = client.post(
        "/capsules", params={"workspace_id": member_b.workspace_id}, headers=member_a.auth_headers
    )
    assert resp.status_code == 403


def test_build_capsule_streams_zip(client, make_user, db_query, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    admin = make_user(role="admin")
    _seed_provenance(db_query, admin.workspace_id)

    resp = client.post(
        "/capsules",
        params={"workspace_id": admin.workspace_id},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert "X-Capsule-Public-Ref" in resp.headers
    assert "X-Capsule-Digest" in resp.headers

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "capsule.json" in zf.namelist()


def test_build_capsule_rejects_unsupported_variant(client, make_user):
    admin = make_user(role="admin")
    resp = client.post(
        "/capsules",
        params={"workspace_id": admin.workspace_id, "variant": "vendor_exit"},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 422


def test_get_manifest_after_build(client, make_user, db_query, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    admin = make_user(role="admin")
    _seed_provenance(db_query, admin.workspace_id)

    build_resp = client.post(
        "/capsules",
        params={"workspace_id": admin.workspace_id},
        headers=admin.auth_headers,
    )
    assert build_resp.status_code == 200
    public_ref = build_resp.headers["X-Capsule-Public-Ref"]

    manifest_resp = client.get(f"/capsules/{public_ref}/manifest", headers=admin.auth_headers)
    assert manifest_resp.status_code == 200, manifest_resp.text
    body = manifest_resp.json()
    assert body["publicRef"] == public_ref
    assert body["recordCount"] == 1
    assert "entries" in body["manifest"]


def test_manifest_not_found_for_unknown_ref(client, make_user):
    admin = make_user(role="admin")
    fake_ref = str(_uuid.uuid4())
    resp = client.get(f"/capsules/{fake_ref}/manifest", headers=admin.auth_headers)
    assert resp.status_code == 404


def test_manifest_workspace_isolation(client, make_user, db_query, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    admin_a = make_user(role="admin")
    admin_b = make_user(role="admin")
    _seed_provenance(db_query, admin_a.workspace_id)

    build_resp = client.post(
        "/capsules",
        params={"workspace_id": admin_a.workspace_id},
        headers=admin_a.auth_headers,
    )
    public_ref = build_resp.headers["X-Capsule-Public-Ref"]

    resp = client.get(f"/capsules/{public_ref}/manifest", headers=admin_b.auth_headers)
    assert resp.status_code == 403
