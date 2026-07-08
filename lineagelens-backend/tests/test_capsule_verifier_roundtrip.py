"""Cross-check: a capsule built by the real backend must pass the standalone
verifier (lineagelens-verifier/), which independently re-implements the
Ed25519 verify and hash-chain recomputation logic (PART 5 #52).

This is the test that guards the two implementations from drifting apart.
Requires `pip install -e lineagelens-verifier/` in the dev environment.
"""
from __future__ import annotations

import os
import tempfile
import uuid as _uuid
from datetime import UTC, datetime

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

import pytest

lineagelens_verifier = pytest.importorskip(
    "lineagelens_verifier",
    reason="lineagelens-verifier is not installed (pip install -e lineagelens-verifier/)",
)

from app.db.models import ProvenanceRecord
from app.services.capsule_service import CapsuleBuildOptions, build_capsule
from app.services.record_lifecycle_service import apply_lifecycle_event
from app.services.integrity_service import compute_content_sha256, compute_prompt_sha256, compute_record_hash


def _seed_and_build(db_query, workspace_id: str, *, redact: bool = False):
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

        if redact:
            await apply_lifecycle_event(
                session, rec, event_type="redaction", authorized_by="tester", reason="pii"
            )
            await session.commit()

        result = await build_capsule(session, workspace_id, CapsuleBuildOptions())
        return result.capsule_bytes

    return db_query(_run)


def test_backend_built_capsule_verifies_standalone(db_query):
    from lineagelens_verifier.verify import STATUS_VALID, verify_capsule

    ws = f"ws-roundtrip-{_uuid.uuid4().hex[:8]}"
    capsule_bytes = _seed_and_build(db_query, ws)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as fh:
        fh.write(capsule_bytes)
        path = fh.name

    try:
        result = verify_capsule(path)
        assert result.status == STATUS_VALID, result.details
        assert result.manifest_ok is True
        assert result.signature_ok is True
        assert result.chain_ok is True
        assert result.key_trust_ok is True
    finally:
        os.unlink(path)


def test_backend_built_capsule_with_redaction_verifies_standalone(db_query):
    from lineagelens_verifier.verify import STATUS_VALID_WITH_REDACTIONS, verify_capsule

    ws = f"ws-roundtrip-{_uuid.uuid4().hex[:8]}"
    capsule_bytes = _seed_and_build(db_query, ws, redact=True)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as fh:
        fh.write(capsule_bytes)
        path = fh.name

    try:
        result = verify_capsule(path)
        assert result.status == STATUS_VALID_WITH_REDACTIONS, result.details
        assert result.chain_ok is True
    finally:
        os.unlink(path)


def test_verifier_vendored_inside_capsule(db_query):
    """The capsule embeds the standalone verifier's own source (PART 5 #52)."""
    import zipfile
    from io import BytesIO

    ws = f"ws-roundtrip-{_uuid.uuid4().hex[:8]}"
    capsule_bytes = _seed_and_build(db_query, ws)

    with zipfile.ZipFile(BytesIO(capsule_bytes)) as zf:
        names = zf.namelist()
        assert any(n.startswith("verifier/") for n in names), (
            "verifier/ source not vendored into the capsule — check "
            "capsule_service.VERIFIER_SOURCE_DIR resolves to lineagelens-verifier/"
        )
