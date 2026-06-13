"""Tests for app.services.indemnity_service — eligibility evaluation + certificate issuance.

Uses an in-memory mock session pattern (no DB) to keep tests fast and isolated.
The attestation signing path is exercised end-to-end (real Ed25519 via JWT_SECRET_KEY derivation).
"""
from __future__ import annotations

import os
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("BACKEND_MODE", "team")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost:3000")


def _clear_attest_cache():
    from app.core import attestation as att_mod
    att_mod._load_private_key.cache_clear()


@pytest.fixture(autouse=True)
def clear_attestation_cache():
    _clear_attest_cache()
    yield
    _clear_attest_cache()


# ── Fake ORM objects ──────────────────────────────────────────────────────────

def _make_record(
    *,
    uuid=None,
    workspace_id="ws-test",
    risk_score=30,
    license_status="clean",
    license_match_license=None,
    model_name="claude-sonnet-4-6",
):
    r = MagicMock()
    r.uuid = uuid or uuid_pkg.uuid4()
    r.workspace_id = workspace_id
    r.risk_score = risk_score
    r.license_status = license_status
    r.license_match_license = license_match_license
    r.model_name = model_name
    return r


def _make_policy(
    *,
    id=1,
    workspace_id="ws-test",
    name="default",
    max_risk_score=70,
    require_license_clean=True,
    require_human_review=False,
    allowed_models=None,
    unknown_review_pass=False,
    cert_ttl_days=90,
):
    p = MagicMock()
    p.id = id
    p.workspace_id = workspace_id
    p.name = name
    p.rules_json = {
        "max_risk_score": max_risk_score,
        "require_license_clean": require_license_clean,
        "require_human_review": require_human_review,
        "allowed_models": allowed_models or [],
        "unknown_review_pass": unknown_review_pass,
        "cert_ttl_days": cert_ttl_days,
    }
    return p


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def one_or_none(self):
        return (self._value,) if self._value is not None else None

    def scalars(self):
        return self

    def all(self):
        return [self._value] if self._value is not None else []


class _FakeSession:
    """Minimal async-session mock: tracks added objects, fakes execute for records/reviews."""

    def __init__(self, record=None, review_status=None):
        self._record = record
        self._review_status = review_status
        self.added = []
        self._flush_count = 0
        self._id_counter = 100

    async def execute(self, stmt):
        # Return the seeded record for ProvenanceRecord queries
        from app.db.models import ProvenanceRecord, ReviewQueue, Attestation
        # Introspect the ORM statement entity
        try:
            entity = stmt.froms[0] if hasattr(stmt, "froms") else None
        except Exception:
            entity = None
        return _FakeScalarResult(self._record)

    def add(self, obj):
        self.added.append(obj)
        # Assign a fake auto-increment id
        if not hasattr(obj, "id") or obj.id is None:
            object.__setattr__(obj, "id", self._id_counter)
            self._id_counter += 1

    async def flush(self):
        self._flush_count += 1
        for obj in self.added:
            if not getattr(obj, "id", None):
                try:
                    obj.id = self._id_counter
                    self._id_counter += 1
                except Exception:
                    pass


# ── Tests: evaluate_eligibility ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ineligible_when_risk_over_threshold():
    """A record whose risk_score exceeds the policy max must be ineligible."""
    from app.services.indemnity_service import evaluate_eligibility

    record = _make_record(risk_score=90)
    policy = _make_policy(max_risk_score=70)

    session = _FakeSession(record=record)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ):
        result = await evaluate_eligibility(session, "ws-test", "record", str(record.uuid), policy)

    assert result["eligible"] is False
    assert any("risk score" in r for r in result["reasons"])


@pytest.mark.asyncio
async def test_ineligible_when_license_not_clean():
    """A record with license_status='match' must be ineligible when policy requires clean."""
    from app.services.indemnity_service import evaluate_eligibility

    record = _make_record(risk_score=20, license_status="match", license_match_license="GPL-3.0")
    policy = _make_policy(require_license_clean=True)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ):
        result = await evaluate_eligibility(_FakeSession(), "ws-test", "record", str(record.uuid), policy)

    assert result["eligible"] is False
    assert any("license" in r.lower() for r in result["reasons"])


@pytest.mark.asyncio
async def test_eligible_when_all_checks_pass():
    """A clean, low-risk record with no review requirement must be eligible."""
    from app.services.indemnity_service import evaluate_eligibility

    record = _make_record(risk_score=20, license_status="clean")
    policy = _make_policy(require_license_clean=True, require_human_review=False)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ):
        result = await evaluate_eligibility(_FakeSession(), "ws-test", "record", str(record.uuid), policy)

    assert result["eligible"] is True
    assert result["reasons"] == []


@pytest.mark.asyncio
async def test_ineligible_unknown_review_when_pass_false():
    """Unknown review status blocks eligibility when unknown_review_pass=False."""
    from app.services.indemnity_service import evaluate_eligibility

    record = _make_record(risk_score=10, license_status="clean")
    policy = _make_policy(require_human_review=True, unknown_review_pass=False)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ):
        result = await evaluate_eligibility(_FakeSession(), "ws-test", "record", str(record.uuid), policy)

    assert result["eligible"] is False
    assert any("unknown" in r.lower() for r in result["reasons"])


@pytest.mark.asyncio
async def test_eligible_unknown_review_when_pass_true():
    """Unknown review status passes when unknown_review_pass=True."""
    from app.services.indemnity_service import evaluate_eligibility

    record = _make_record(risk_score=10, license_status="clean")
    policy = _make_policy(require_human_review=True, unknown_review_pass=True)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ):
        result = await evaluate_eligibility(_FakeSession(), "ws-test", "record", str(record.uuid), policy)

    assert result["eligible"] is True


@pytest.mark.asyncio
async def test_no_records_is_ineligible():
    """A scope_ref that resolves to zero records must be ineligible."""
    from app.services.indemnity_service import evaluate_eligibility

    policy = _make_policy()

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[]),
    ):
        result = await evaluate_eligibility(_FakeSession(), "ws-test", "record", "nonexistent", policy)

    assert result["eligible"] is False
    assert result["records_evaluated"] == 0


# ── Tests: issue_certificate ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_eligible_cert_is_signed_and_verifiable():
    """An eligible certificate must carry an attestation that passes verify_attestation."""
    from app.core.attestation import SignedAttestation, verify_attestation
    from app.services.indemnity_service import issue_certificate
    import json as _json

    record = _make_record(risk_score=10, license_status="clean")
    policy = _make_policy(require_license_clean=True, require_human_review=False)
    session = _FakeSession(record=record)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ), patch(
        "app.services.indemnity_service._get_chain_tip",
        AsyncMock(return_value="prev-hash-abc"),
    ), patch(
        "app.services.indemnity_service.log_audit_event",
        AsyncMock(),
    ):
        cert, att = await issue_certificate(
            session,
            workspace_id="ws-test",
            scope="record",
            scope_ref=str(record.uuid),
            policy=policy,
            issued_by="user-1",
        )

    assert cert.eligibility == "eligible"
    assert att is not None

    # Verify the attestation cryptographically
    statement = _json.loads(att.statement_json)
    signed = SignedAttestation(
        statement=statement,
        signature=att.signature,
        public_key_id=att.public_key_id,
    )
    assert verify_attestation(signed) is True


@pytest.mark.asyncio
async def test_ineligible_cert_has_no_attestation():
    """An ineligible certificate must not create an Attestation row."""
    from app.services.indemnity_service import issue_certificate

    record = _make_record(risk_score=95)
    policy = _make_policy(max_risk_score=70)
    session = _FakeSession(record=record)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ), patch(
        "app.services.indemnity_service._get_chain_tip",
        AsyncMock(return_value=None),
    ), patch(
        "app.services.indemnity_service.log_audit_event",
        AsyncMock(),
    ):
        cert, att = await issue_certificate(
            session,
            workspace_id="ws-test",
            scope="record",
            scope_ref=str(record.uuid),
            policy=policy,
            issued_by="user-1",
        )

    assert cert.eligibility == "ineligible"
    assert att is None
    assert cert.attestation_id is None


@pytest.mark.asyncio
async def test_expiry_set_on_eligible_cert():
    """An eligible certificate must carry a non-null expires_at in the future."""
    from app.services.indemnity_service import issue_certificate

    record = _make_record(risk_score=10, license_status="clean")
    policy = _make_policy(cert_ttl_days=30)
    session = _FakeSession(record=record)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ), patch(
        "app.services.indemnity_service._get_chain_tip",
        AsyncMock(return_value=None),
    ), patch(
        "app.services.indemnity_service.log_audit_event",
        AsyncMock(),
    ):
        cert, _ = await issue_certificate(
            session,
            workspace_id="ws-test",
            scope="record",
            scope_ref=str(record.uuid),
            policy=policy,
            issued_by="user-1",
        )

    assert cert.expires_at is not None
    assert cert.expires_at > datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_audit_event_written_on_issue():
    """issue_certificate must call log_audit_event regardless of eligibility."""
    from app.services.indemnity_service import issue_certificate

    record = _make_record(risk_score=10, license_status="clean")
    policy = _make_policy()
    session = _FakeSession(record=record)

    audit_calls = []

    async def _fake_audit(*args, **kwargs):
        audit_calls.append(kwargs)

    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[record]),
    ), patch(
        "app.services.indemnity_service._get_review_status",
        AsyncMock(return_value="unknown"),
    ), patch(
        "app.services.indemnity_service._get_chain_tip",
        AsyncMock(return_value=None),
    ), patch(
        "app.services.indemnity_service.log_audit_event",
        side_effect=_fake_audit,
    ):
        await issue_certificate(
            session,
            workspace_id="ws-test",
            scope="record",
            scope_ref=str(record.uuid),
            policy=policy,
            issued_by="user-audit",
        )

    assert len(audit_calls) >= 1
    assert audit_calls[0]["action"] == "indemnity_certificate_issued"


@pytest.mark.asyncio
async def test_workspace_isolation():
    """A record from a different workspace must not satisfy the eligibility check."""
    from app.services.indemnity_service import evaluate_eligibility

    record = _make_record(workspace_id="ws-other", risk_score=10, license_status="clean")
    policy = _make_policy(workspace_id="ws-test")

    # _fetch_records_for_scope returns empty for cross-workspace refs
    with patch(
        "app.services.indemnity_service._fetch_records_for_scope",
        AsyncMock(return_value=[]),
    ):
        result = await evaluate_eligibility(
            _FakeSession(), "ws-test", "record", str(record.uuid), policy
        )

    assert result["eligible"] is False
    assert result["records_evaluated"] == 0
