"""Tests for app.core.attestation — Ed25519 signing core.

All tests are pure-function unit tests (no DB, no server).
They patch the ATTESTATION_SIGNING_KEY env var so lru_cache is cleared
between test invocations.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace as dc_replace
from unittest import mock

import pytest

# Ensure JWT_SECRET_KEY is available for the dev-key derivation path.
os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")


def _clear_key_cache():
    from app.core import attestation as att_mod
    att_mod._load_private_key.cache_clear()


@pytest.fixture(autouse=True)
def clear_cache():
    _clear_key_cache()
    yield
    _clear_key_cache()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_and_sign(claims: dict, workspace: str = "ws-test", subject_id: str = "sub-1"):
    from app.core.attestation import build_attestation, sign_attestation
    statement = build_attestation("record", subject_id, claims, workspace_id=workspace)
    return sign_attestation(statement)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_sign_verify_roundtrip():
    """A freshly signed attestation must verify successfully."""
    from app.core.attestation import verify_attestation
    signed = _build_and_sign({"foo": "bar"})
    assert verify_attestation(signed) is True


def test_tamper_claim_fails_verify():
    """Mutating any claim after signing must break verification."""
    from app.core.attestation import SignedAttestation, verify_attestation
    signed = _build_and_sign({"foo": "bar"})
    # Deep-copy statement, mutate one claim
    tampered_stmt = json.loads(json.dumps(signed.statement))
    tampered_stmt["claims"]["foo"] = "TAMPERED"
    tampered = SignedAttestation(
        statement=tampered_stmt,
        signature=signed.signature,
        public_key_id=signed.public_key_id,
    )
    assert verify_attestation(tampered) is False


def test_tamper_issuer_fails_verify():
    """Mutating the issuer field must break verification."""
    from app.core.attestation import SignedAttestation, verify_attestation
    signed = _build_and_sign({"x": 1})
    tampered_stmt = json.loads(json.dumps(signed.statement))
    tampered_stmt["issuer"] = "evil-issuer"
    tampered = SignedAttestation(
        statement=tampered_stmt,
        signature=signed.signature,
        public_key_id=signed.public_key_id,
    )
    assert verify_attestation(tampered) is False


def test_chain_linkage_stored_in_statement():
    """prev_hash is stored verbatim in the statement so the chain is auditable."""
    from app.core.attestation import build_attestation
    prev = "abc123def456"
    stmt = build_attestation("record", "id-1", {}, workspace_id="ws", prev_hash=prev)
    assert stmt["prev_hash"] == prev


def test_chain_linkage_empty_when_omitted():
    """Omitting prev_hash stores an empty string (not None) for canonical JSON stability."""
    from app.core.attestation import build_attestation
    stmt = build_attestation("record", "id-1", {}, workspace_id="ws")
    assert stmt["prev_hash"] == ""


def test_statement_keys_are_stable():
    """Canonical JSON must be identical regardless of claims insertion order."""
    from app.core.attestation import build_attestation
    s1 = build_attestation("record", "x", {"b": 2, "a": 1}, workspace_id="ws")
    s2 = build_attestation("record", "x", {"a": 1, "b": 2}, workspace_id="ws")
    # Only issued_at will differ; strip it to compare structure
    s1.pop("issued_at")
    s2.pop("issued_at")
    assert json.dumps(s1, sort_keys=True) == json.dumps(s2, sort_keys=True)


def test_public_key_hex_is_64_chars():
    """Raw 32-byte Ed25519 public key encodes to 64 hex chars."""
    from app.core.attestation import get_public_key_hex
    assert len(get_public_key_hex()) == 64


def test_dev_key_derived_when_signing_key_unset():
    """With no ATTESTATION_SIGNING_KEY, a key is derived from JWT_SECRET_KEY."""
    with mock.patch.dict(os.environ, {"ATTESTATION_SIGNING_KEY": ""}):
        _clear_key_cache()
        from app.core.attestation import verify_attestation
        signed = _build_and_sign({"dev": True})
        assert verify_attestation(signed) is True
    _clear_key_cache()


def test_explicit_signing_key_overrides_derivation():
    """An explicit 32-byte base64 key is loaded and produces a valid signature."""
    import base64
    seed = b"A" * 32
    b64_key = base64.b64encode(seed).decode()
    with mock.patch.dict(os.environ, {"ATTESTATION_SIGNING_KEY": b64_key}):
        _clear_key_cache()
        from app.core.attestation import verify_attestation
        signed = _build_and_sign({"explicit": True})
        assert verify_attestation(signed) is True
    _clear_key_cache()


def test_wrong_length_signing_key_raises():
    """A base64 value that decodes to the wrong length must raise RuntimeError."""
    import base64
    bad_key = base64.b64encode(b"tooshort").decode()
    with mock.patch.dict(os.environ, {"ATTESTATION_SIGNING_KEY": bad_key}):
        _clear_key_cache()
        with pytest.raises(RuntimeError, match="invalid"):
            from app.core.attestation import _load_private_key
            _load_private_key()
    _clear_key_cache()
