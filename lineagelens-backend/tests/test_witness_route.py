"""Route-level tests for /witness (PART 5 #53)."""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from app.db.models import ProvenanceRecord
from app.services.integrity_service import compute_content_sha256, compute_prompt_sha256, compute_record_hash


def _seed_chained_record(db_query, workspace_id: str):
    async def _run(session):
        rid = _uuid.uuid4()
        rec = ProvenanceRecord(
            uuid=rid,
            workspace_id=workspace_id,
            file_path="f.py",
            timestamp_iso=datetime.now(UTC),
            inserted_code="x = 1",
            provenance_payload={},
            model_name="m",
        )
        session.add(rec)
        await session.flush()
        await session.refresh(rec)
        prompt_sha = compute_prompt_sha256(None)
        rec.content_sha256 = compute_content_sha256("x = 1")
        rec.record_hash = compute_record_hash(
            record_uuid=str(rid),
            workspace_id=workspace_id,
            file_path="f.py",
            inserted_code="x = 1",
            model_name="m",
            prompt_sha256=prompt_sha,
            timestamp_iso=rec.timestamp_iso.isoformat(),
            prev_hash=None,
        )
        await session.commit()

    db_query(_run)


def test_publish_returns_all_backends_even_unconfigured(client, make_user, db_query):
    admin = make_user(role="admin")
    _seed_chained_record(db_query, admin.workspace_id)

    resp = client.post("/witness/publish", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "rootHash" in body
    assert len(body["receipts"]) == 4
    assert all(r["status"] == "not_configured" for r in body["receipts"])


def test_publish_persists_receipts_queryable_via_get(client, make_user, db_query):
    admin = make_user(role="admin")
    _seed_chained_record(db_query, admin.workspace_id)

    client.post("/witness/publish", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)

    resp = client.get("/witness/receipts", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 4


def test_publish_workspace_isolation(client, make_user):
    admin_a = make_user(role="admin")
    admin_b = make_user(role="admin")
    resp = client.post("/witness/publish", params={"workspace_id": admin_b.workspace_id}, headers=admin_a.auth_headers)
    assert resp.status_code == 403
