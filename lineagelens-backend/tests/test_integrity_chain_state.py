"""Tests for the explicit machine-readable chainState field (PART 5 #58).

/integrity/verify already reported ok/states in prose-adjacent form; this
adds a stable chainState enum value so callers don't have to infer the
outcome from the states dict + ok boolean.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import UTC, datetime

from app.core.assurance_states import ChainState
from app.db.models import ProvenanceRecord
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
    compute_record_hash,
)


def _seed_chained_record(db_query, workspace_id: str, *, code: str = "x = 1", prompt=None):
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
        rec.prev_hash = None
        rec.record_hash = compute_record_hash(
            record_uuid=str(rid),
            workspace_id=workspace_id,
            file_path="f.py",
            inserted_code=code,
            model_name="m",
            prompt_sha256=prompt_sha,
            timestamp_iso=rec.timestamp_iso.isoformat(),
            prev_hash=None,
        )
        await session.commit()
        return str(rid)

    return db_query(_run)


def test_no_records_reports_unverifiable(client, make_user):
    admin = make_user(role="admin")
    resp = client.get(
        "/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["chainState"] == ChainState.UNVERIFIABLE.value


def test_all_active_records_report_fully_available(client, make_user, db_query):
    admin = make_user(role="admin")
    _seed_chained_record(db_query, admin.workspace_id, prompt=[{"role": "user", "content": "hi"}])

    resp = client.get(
        "/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["chainState"] == ChainState.FULLY_AVAILABLE.value


def test_redacted_record_reports_locally_tamper_evident(client, make_user, db_query):
    admin = make_user(role="admin")
    rid = _seed_chained_record(
        db_query, admin.workspace_id, prompt=[{"role": "user", "content": "secret"}]
    )
    r = client.patch(f"/provenance/{rid}/redact", headers=admin.auth_headers)
    assert r.status_code == 200, r.text

    resp = client.get(
        "/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["chainState"] == ChainState.LOCALLY_TAMPER_EVIDENT.value


def test_tampered_chain_reports_tampered(client, make_user, db_query):
    admin = make_user(role="admin")
    rid = _seed_chained_record(db_query, admin.workspace_id, prompt=[{"role": "user", "content": "p"}])

    async def _tamper(session):
        from sqlalchemy import select
        rec = (
            await session.execute(
                select(ProvenanceRecord).where(ProvenanceRecord.uuid == _uuid.UUID(rid))
            )
        ).scalar_one()
        rec.prompt_messages = None
        rec.lifecycle_state = "redacted"
        rec.is_redacted = True
        await session.commit()

    db_query(_tamper)

    resp = client.get(
        "/integrity/verify", params={"workspace_id": admin.workspace_id}, headers=admin.auth_headers
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["chainState"] == ChainState.TAMPERED.value
