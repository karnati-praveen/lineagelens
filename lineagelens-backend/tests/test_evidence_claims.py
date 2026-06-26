"""Tests for the typed evidence/claim model (PART 1 #7).

Different kinds of evidence (observed / correlated / declared / derived /
unknown) must be tagged distinctly and never collapsed into one green check.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.evidence import (
    CLAIM_CLASSES,
    DECLARED,
    DERIVED,
    OBSERVED,
    UNKNOWN,
    classify_record_claims,
)


def _rec(**kw):
    base = dict(
        file_path="a.py",
        inserted_code="x=1",
        model_name="claude-opus-4-8",
        prompt_messages=[{"role": "user", "content": "hi"}],
        prompt_sha256="deadbeef",
        record_hash="abc123",
        lifecycle_state="active",
        risk_score=42,
        license_status="clean_within_corpus",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _by_field(claims):
    return {c["field"]: c for c in claims}


def test_all_claim_classes_are_valid():
    claims = classify_record_claims(_rec())
    for c in claims:
        assert c["claimClass"] in CLAIM_CLASSES


def test_model_name_is_declared_not_observed():
    claims = _by_field(classify_record_claims(_rec()))
    assert claims["model_name"]["claimClass"] == DECLARED


def test_risk_score_is_derived():
    claims = _by_field(classify_record_claims(_rec()))
    assert claims["risk_score"]["claimClass"] == DERIVED


def test_no_corpus_license_is_unknown_not_clean():
    claims = _by_field(classify_record_claims(_rec(license_status="not_configured")))
    assert claims["license_status"]["claimClass"] == UNKNOWN


def test_inserted_code_is_observed():
    claims = _by_field(classify_record_claims(_rec()))
    assert claims["inserted_code"]["claimClass"] == OBSERVED


def test_redacted_prompt_is_correlated_digest_only():
    claims = _by_field(classify_record_claims(
        _rec(prompt_messages=None, prompt_sha256="deadbeef", lifecycle_state="redacted")
    ))
    assert claims["prompt"]["claimClass"] == "correlated"
    assert claims["prompt"]["limitations"]


def test_missing_prompt_no_digest_is_unknown():
    claims = _by_field(classify_record_claims(
        _rec(prompt_messages=None, prompt_sha256=None)
    ))
    assert claims["prompt"]["claimClass"] == UNKNOWN


def test_client_commitment_matches(monkeypatch):
    """PART 2 #17 — a matching proxy commitment is cross-checked as 'matched'."""
    import hashlib
    code = "print('hi')"
    sha = hashlib.sha256(code.encode()).hexdigest()
    rec = _rec(
        inserted_code=code,
        content_sha256=sha,
        provenance_payload={"commitments": {"insertedTextSha256": sha, "committedBy": "lineagelens-proxy"}},
    )
    claims = _by_field(classify_record_claims(rec))
    assert claims["client_commitment"]["value"] == "matched"
    assert claims["client_commitment"]["claimClass"] == "correlated"


def test_client_commitment_mismatch_is_flagged():
    """A proxy commitment that disagrees with stored content is flagged (possible tampering)."""
    rec = _rec(
        inserted_code="print('hi')",
        content_sha256="0" * 64,
        provenance_payload={"commitments": {"insertedTextSha256": "f" * 64}},
    )
    claims = _by_field(classify_record_claims(rec))
    assert claims["client_commitment"]["value"] == "mismatch"
    assert claims["client_commitment"]["limitations"]
