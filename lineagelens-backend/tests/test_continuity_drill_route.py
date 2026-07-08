"""Route-level tests for /continuity-drills (PART 5 #55)."""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from app.db.models import ProvenanceRecord
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
    compute_record_hash,
)


def _seed_record(db_query, workspace_id: str) -> str:
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
        await session.flush()
        await session.refresh(rec)
        prompt_sha = compute_prompt_sha256(rec.prompt_messages)
        rec.prompt_sha256 = prompt_sha
        rec.content_sha256 = compute_content_sha256(rec.inserted_code)
        rec.record_hash = compute_record_hash(
            record_uuid=str(rid),
            workspace_id=workspace_id,
            file_path="f.py",
            inserted_code="x = 1",
            model_name="gpt-4o",
            prompt_sha256=prompt_sha,
            timestamp_iso=rec.timestamp_iso.isoformat(),
            prev_hash=None,
        )
        await session.commit()
        return str(rid)

    return db_query(_run)


def test_trigger_drill_and_fetch_result(client, make_user, db_query, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    admin = make_user(role="admin")
    _seed_record(db_query, admin.workspace_id)

    resp = client.post(
        "/continuity-drills", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["overallStatus"] in ("green", "amber", "red")
    assert len(body["steps"]) == 6
    assert body["signature"] is not None

    public_ref = body["publicRef"]
    get_resp = client.get(f"/continuity-drills/{public_ref}", headers=admin.auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["publicRef"] == public_ref


def test_list_drills(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    admin = make_user(role="admin")
    client.post("/continuity-drills", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)

    resp = client.get("/continuity-drills", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1


def test_cannot_fetch_drill_from_other_workspace(client, make_user, tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSULE_STORAGE_DIR", str(tmp_path / "capsules"))
    admin_a = make_user(role="admin")
    admin_b = make_user(role="admin")

    build_resp = client.post(
        "/continuity-drills", params={"workspace_id": admin_a.workspace_id}, headers=admin_a.auth_headers
    )
    public_ref = build_resp.json()["publicRef"]

    resp = client.get(f"/continuity-drills/{public_ref}", headers=admin_b.auth_headers)
    assert resp.status_code == 403


def test_drill_not_found(client, make_user):
    admin = make_user(role="admin")
    resp = client.get(f"/continuity-drills/{_uuid.uuid4()}", headers=admin.auth_headers)
    assert resp.status_code == 404
