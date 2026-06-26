"""Tests for the non-destructive privacy lifecycle (PART 2 #10 & #11).

Regression focus:
  * #10 — redacting a record (which nulls prompt_messages) must NOT make
    /integrity/verify report "tampered".  It must report validly_redacted.
  * #11 — deletion is a tombstone: the row and its chain link survive, content
    is scrubbed, and the chain still verifies.

Fixtures: client, make_user, db_query from conftest.py (team mode).
"""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from app.db.models import ProvenanceRecord
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
    compute_record_hash,
)


def _seed_chained_record(
    db_query,
    workspace_id: str,
    *,
    prev_hash: str | None = None,
    code: str = "x = 1",
    prompt=None,
):
    """Insert a fully hash-chained ProvenanceRecord. Returns (uuid, record_hash).

    The hash is computed from the *persisted* timestamp so a verify recompute
    matches exactly (avoids datetime round-trip drift).
    """
    async def _run(session):
        rid = _uuid.uuid4()
        rec = ProvenanceRecord(
            uuid=rid,
            workspace_id=workspace_id,
            file_path="f.py",
            timestamp_iso=datetime.now(UTC),
            inserted_code=code,
            provenance_payload={},
            prompt_messages=prompt,
            model_name="m",
        )
        session.add(rec)
        await session.flush()
        await session.refresh(rec)

        prompt_sha = compute_prompt_sha256(prompt)
        rec.prompt_sha256 = prompt_sha
        rec.content_sha256 = compute_content_sha256(code)
        rec.prev_hash = prev_hash
        rec.record_hash = compute_record_hash(
            record_uuid=str(rid),
            workspace_id=workspace_id,
            file_path="f.py",
            inserted_code=code,
            model_name="m",
            prompt_sha256=prompt_sha,
            timestamp_iso=rec.timestamp_iso.isoformat(),
            prev_hash=prev_hash,
        )
        await session.commit()
        return str(rid), rec.record_hash

    return db_query(_run)


def _get_record(db_query, workspace_id: str, record_uuid: str):
    async def _fetch(session):
        from sqlalchemy import select
        result = await session.execute(
            select(ProvenanceRecord).where(
                ProvenanceRecord.uuid == _uuid.UUID(record_uuid),
                ProvenanceRecord.workspace_id == workspace_id,
            )
        )
        return result.scalar_one_or_none()
    return db_query(_fetch)


def test_chained_record_verifies_clean(client, make_user, db_query):
    admin = make_user(role="admin")
    _seed_chained_record(db_query, admin.workspace_id, prompt=[{"role": "user", "content": "hi"}])

    resp = client.get("/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["states"]["active"] == 1


def test_redacted_record_is_validly_redacted_not_tampered(client, make_user, db_query):
    """The core #10 regression: redaction nulls prompt_messages but verify stays OK."""
    admin = make_user(role="admin")
    rid, _ = _seed_chained_record(
        db_query, admin.workspace_id, prompt=[{"role": "user", "content": "secret prompt"}]
    )

    # Redact (nulls prompt_messages — the exact condition that used to break verify).
    r = client.patch(f"/provenance/{rid}/redact", headers=admin.auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle_state"] == "redacted"

    rec = _get_record(db_query, admin.workspace_id, rid)
    assert rec.prompt_messages is None  # scrubbed
    assert rec.record_hash is not None  # chain hash untouched

    resp = client.get("/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body
    assert body["states"]["validly_redacted"] == 1
    assert body["states"]["active"] == 0


def test_deleted_record_tombstone_keeps_chain(client, make_user, db_query):
    """#11: deletion scrubs content but the chained row + linkage survive and verify."""
    admin = make_user(role="admin")
    rid, _ = _seed_chained_record(db_query, admin.workspace_id, code="secret()", prompt=None)

    r = client.delete(f"/provenance/{rid}", headers=admin.auth_headers)
    assert r.status_code == 204, r.text

    rec = _get_record(db_query, admin.workspace_id, rid)
    assert rec is not None  # tombstone retained, not physically deleted
    assert rec.lifecycle_state == "deleted"
    assert rec.inserted_code == ""  # content scrubbed

    resp = client.get("/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    body = resp.json()
    assert body["ok"] is True, body
    assert body["states"]["validly_deleted"] == 1


def test_scrubbed_content_without_event_is_tampered(client, make_user, db_query):
    """If content is scrubbed and lifecycle_state set WITHOUT a signed event, verify flags tampering."""
    admin = make_user(role="admin")
    rid, _ = _seed_chained_record(db_query, admin.workspace_id, prompt=[{"role": "user", "content": "p"}])

    # Simulate a rogue DBA: mark redacted + null content but write NO lifecycle event.
    async def _tamper(session):
        from sqlalchemy import select
        rec = (await session.execute(
            select(ProvenanceRecord).where(ProvenanceRecord.uuid == _uuid.UUID(rid))
        )).scalar_one()
        rec.prompt_messages = None
        rec.lifecycle_state = "redacted"
        rec.is_redacted = True
        await session.commit()
    db_query(_tamper)

    resp = client.get("/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers)
    body = resp.json()
    assert body["ok"] is False
    assert body["first_break_uuid"] == rid
