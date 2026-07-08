"""Tests for the Evidence Capsule service (PART 5 #51).

Builds a small fixture workspace touching every capsule section (a chained
provenance record, a redaction lifecycle event, a policy version, an outcome,
a recall campaign, a human review, an audit log entry) and asserts the
capsule bundles all of it, is internally consistent (manifest hashes match
the actual zip bytes), and is Ed25519-signed and verifiable.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid as _uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

from app.core.attestation import SignedAttestation, verify_attestation
from app.db.models import Policy, ProvenanceRecord
from app.services.capsule_service import CapsuleBuildOptions, build_capsule
from app.services.human_review_service import record_review
from app.services.integrity_service import (
    compute_content_sha256,
    compute_prompt_sha256,
    compute_record_hash,
)
from app.services.outcome_service import record_outcome
from app.services.policy_version_service import append_version
from app.services.recall_service import BlastRadiusResult, open_recall
from app.services.record_lifecycle_service import apply_lifecycle_event


def _seed_full_workspace(db_query, workspace_id: str) -> str:
    async def _run(session):
        rid = _uuid.uuid4()
        rec = ProvenanceRecord(
            uuid=rid,
            workspace_id=workspace_id,
            file_path="f.py",
            timestamp_iso=datetime.now(UTC),
            inserted_code="x = 1",
            provenance_payload={},
            prompt_messages=[{"role": "user", "content": "add x"}],
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

        # Policy + version
        policy = Policy(
            workspace_id=workspace_id,
            name="no-secrets",
            policy_type="risk_rule",
            config={"pattern": "secret"},
            action="flag",
        )
        session.add(policy)
        await session.flush()
        await append_version(session, policy, change_type="create", created_by="tester")
        await session.commit()

        # Outcome
        await record_outcome(
            session,
            workspace_id=workspace_id,
            record_uuid=str(rid),
            outcome_type="survived",
            source="ci",
            user_id="tester",
        )

        # Recall campaign
        await open_recall(
            session,
            workspace_id,
            "tester",
            {"model": "gpt-4o"},
            1,
            member_uuids=[str(rid)],
            blast=BlastRadiusResult(descendant_uuids=[], coverage_status="unavailable"),
        )

        # Human review
        await record_review(
            session,
            workspace_id=workspace_id,
            scope_ref=str(rid),
            reviewer_user_id="tester",
            lines_reviewed=10,
            seconds_on_diff=60,
            comment_count=1,
            verdict="approved",
        )
        await session.commit()

        # Redact (lifecycle event)
        await apply_lifecycle_event(
            session, rec, event_type="redaction", authorized_by="tester", reason="pii"
        )
        await session.commit()

        return str(rid)

    return db_query(_run)


def _build(db_query, workspace_id: str, **kwargs):
    async def _run(session):
        options = CapsuleBuildOptions(**kwargs)
        return await build_capsule(session, workspace_id, options)

    return db_query(_run)


def test_capsule_bundles_every_section(db_query):
    ws = f"ws-capsule-{_uuid.uuid4().hex[:8]}"
    rid = _seed_full_workspace(db_query, ws)

    result = _build(db_query, ws)

    with zipfile.ZipFile(BytesIO(result.capsule_bytes)) as zf:
        names = set(zf.namelist())
        assert "capsule.json" in names
        assert "capsule.json.sig" in names
        assert "manifest.json" in names

        capsule_doc = json.loads(zf.read("capsule.json"))

    assert capsule_doc["workspaceId"] == ws
    assert capsule_doc["scope"]["recordCount"] == 1
    assert len(capsule_doc["agentTraceDocuments"]) == 1
    assert len(capsule_doc["recordChain"]) == 1
    assert capsule_doc["recordChain"][0]["uuid"] == rid
    assert capsule_doc["recordChain"][0]["recordHash"] is not None
    assert rid in capsule_doc["claims"]
    assert len(capsule_doc["policyVersions"]) == 1
    assert len(capsule_doc["lifecycleEvents"]) == 1
    assert capsule_doc["lifecycleEvents"][0]["eventType"] == "redaction"
    assert capsule_doc["lifecycleEvents"][0]["signatureValid"] is True
    assert len(capsule_doc["outcomeEvents"]) == 1
    assert len(capsule_doc["recallEvents"]) == 1
    assert len(capsule_doc["reviewEvents"]) == 1
    assert capsule_doc["aibom"]["summary"]["total_records"] == 1
    assert capsule_doc["licenseCorpus"]["configured"] in (True, False)
    assert capsule_doc["versions"]["capsuleSchemaVersion"] == "1.0"


def test_capsule_manifest_hashes_match_actual_zip_bytes(db_query):
    ws = f"ws-capsule-{_uuid.uuid4().hex[:8]}"
    _seed_full_workspace(db_query, ws)
    result = _build(db_query, ws)

    with zipfile.ZipFile(BytesIO(result.capsule_bytes)) as zf:
        for entry in result.manifest["entries"]:
            content = zf.read(entry["path"])
            assert hashlib.sha256(content).hexdigest() == entry["sha256"]
            assert len(content) == entry["sizeBytes"]


def test_capsule_signature_verifies(db_query):
    ws = f"ws-capsule-{_uuid.uuid4().hex[:8]}"
    _seed_full_workspace(db_query, ws)
    result = _build(db_query, ws)

    with zipfile.ZipFile(BytesIO(result.capsule_bytes)) as zf:
        capsule_doc = json.loads(zf.read("capsule.json"))

    signed = SignedAttestation(
        statement=capsule_doc,
        signature=result.signature,
        public_key_id=result.public_key_id,
    )
    assert verify_attestation(signed) is True


def test_capsule_signature_detects_tampering(db_query):
    ws = f"ws-capsule-{_uuid.uuid4().hex[:8]}"
    _seed_full_workspace(db_query, ws)
    result = _build(db_query, ws)

    with zipfile.ZipFile(BytesIO(result.capsule_bytes)) as zf:
        capsule_doc = json.loads(zf.read("capsule.json"))

    capsule_doc["workspaceId"] = "tampered-workspace"
    signed = SignedAttestation(
        statement=capsule_doc,
        signature=result.signature,
        public_key_id=result.public_key_id,
    )
    assert verify_attestation(signed) is False


def test_unsupported_variant_rejected(db_query):
    ws = f"ws-capsule-{_uuid.uuid4().hex[:8]}"

    async def _run(session):
        options = CapsuleBuildOptions(variant="redacted_legal")
        try:
            await build_capsule(session, ws, options)
            return None
        except ValueError as exc:
            return str(exc)

    err = db_query(_run)
    assert err is not None
    assert "redacted_legal" in err


def test_empty_workspace_produces_empty_but_valid_capsule(db_query):
    ws = f"ws-empty-{_uuid.uuid4().hex[:8]}"
    result = _build(db_query, ws)

    with zipfile.ZipFile(BytesIO(result.capsule_bytes)) as zf:
        capsule_doc = json.loads(zf.read("capsule.json"))

    assert capsule_doc["scope"]["recordCount"] == 0
    assert capsule_doc["agentTraceDocuments"] == []
    assert capsule_doc["recordChain"] == []
    assert capsule_doc["claims"] == {}
