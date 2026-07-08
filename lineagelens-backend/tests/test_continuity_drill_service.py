"""Tests for the provenance continuity drill (PART 5 #55).

Runs the full drill against a workspace with no Neo4j configured (as in any
plain dev/test environment) and asserts the honest rollup: the Neo4j step is
`skipped_not_configured` (never `passed`), every other step passes, and the
overall status is `amber` (not a fake `green`).
"""
from __future__ import annotations

import os
import uuid as _uuid
from datetime import UTC, datetime

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

from app.db.models import ProvenanceRecord
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
    compute_record_hash,
)
from app.services.continuity_drill_service import (
    FAILED,
    PASSED,
    SKIPPED_NOT_CONFIGURED,
    STEP_EMBEDDING_FALLBACK,
    STEP_EXPORT_CAPSULE,
    STEP_KEY_ROTATION,
    STEP_REBUILD_GRAPH,
    STEP_VENDOR_FALLBACK,
    STEP_VERIFY_OFFLINE,
    run_continuity_drill,
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


def _run_drill(db_query, workspace_id: str, neo4j_service=None):
    async def _run(session):
        return await run_continuity_drill(session, workspace_id, neo4j_service=neo4j_service)

    return db_query(_run)


def test_drill_without_neo4j_reports_honest_amber_rollup(db_query):
    ws = f"ws-drill-{_uuid.uuid4().hex[:8]}"
    _seed_record(db_query, ws)

    result = _run_drill(db_query, ws)
    by_step = {s.step: s.status for s in result.steps}

    details = {s.step: s.details for s in result.steps}
    assert by_step[STEP_EXPORT_CAPSULE] == PASSED
    assert by_step[STEP_VERIFY_OFFLINE] in (PASSED, SKIPPED_NOT_CONFIGURED), details[STEP_VERIFY_OFFLINE]
    assert by_step[STEP_REBUILD_GRAPH] == SKIPPED_NOT_CONFIGURED, "must never claim graph rebuild passed with no Neo4j"
    assert by_step[STEP_KEY_ROTATION] == PASSED
    assert by_step[STEP_EMBEDDING_FALLBACK] == PASSED
    assert by_step[STEP_VENDOR_FALLBACK] == PASSED
    assert FAILED not in by_step.values()
    assert result.overall_status == "amber"


def test_drill_result_is_signed(db_query):
    ws = f"ws-drill-{_uuid.uuid4().hex[:8]}"
    _seed_record(db_query, ws)

    result = _run_drill(db_query, ws)
    assert result.signature is not None
    assert result.public_key_id is not None
    assert len(result.signature) == 128  # 64-byte Ed25519 sig, hex-encoded
    assert len(result.public_key_id) == 16


def test_drill_with_neo4j_configured_attempts_rebuild(db_query):
    ws = f"ws-drill-{_uuid.uuid4().hex[:8]}"
    _seed_record(db_query, ws)

    class _FakeNeo4jService:
        async def rebuild_projection(self, records):
            return {"nodesRebuilt": len(records), "limitation": "test-fake"}

    result = _run_drill(db_query, ws, neo4j_service=_FakeNeo4jService())
    by_step = {s.step: s.status for s in result.steps}
    assert by_step[STEP_REBUILD_GRAPH] == PASSED
    assert result.overall_status == "green"


def test_drill_reports_failed_rebuild_not_silently_skipped(db_query):
    ws = f"ws-drill-{_uuid.uuid4().hex[:8]}"
    _seed_record(db_query, ws)

    class _BrokenNeo4jService:
        async def rebuild_projection(self, records):
            raise RuntimeError("connection refused")

    result = _run_drill(db_query, ws, neo4j_service=_BrokenNeo4jService())
    by_step = {s.step: s.status for s in result.steps}
    assert by_step[STEP_REBUILD_GRAPH] == FAILED
    assert result.overall_status == "red"
