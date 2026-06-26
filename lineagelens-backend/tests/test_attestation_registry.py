"""Tests for the attestation key registry (PART 3 #19).

Historical/multi-key verification + validity windows + compromise timestamps:
a signature must be rejected if the signing key was retired/expired/compromised
at signing time, even when the cryptographic signature itself checks out.
"""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.attestation import (
    SignedAttestation,
    _registry_clear_cache,
    verify_attestation,
    verify_attestation_detailed,
)


def _key():
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    import hashlib
    pkid = hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:16]
    return priv, pub_hex, pkid


def _sign(priv, statement) -> str:
    canonical = json.dumps(statement, sort_keys=True, default=str).encode()
    return priv.sign(canonical).hex()


def _set_registry(monkeypatch, entries):
    monkeypatch.setenv("ATTESTATION_KEY_REGISTRY", json.dumps(entries))
    _registry_clear_cache()


@pytest.fixture(autouse=True)
def _clear():
    _registry_clear_cache()
    yield
    _registry_clear_cache()


_STMT = {"issued_at": "2026-06-26T00:00:00+00:00", "issuer": "x", "claims": {"a": 1}}


def test_historical_key_verifies(monkeypatch):
    priv, pub_hex, pkid = _key()
    _set_registry(monkeypatch, [{"publicKeyId": pkid, "publicKeyHex": pub_hex, "status": "active"}])
    signed = SignedAttestation(_STMT, _sign(priv, _STMT), pkid)
    assert verify_attestation(signed) is True


def test_compromised_key_rejected(monkeypatch):
    priv, pub_hex, pkid = _key()
    _set_registry(monkeypatch, [{
        "publicKeyId": pkid, "publicKeyHex": pub_hex,
        "compromisedAt": "2026-06-01T00:00:00+00:00",  # before issued_at
    }])
    signed = SignedAttestation(_STMT, _sign(priv, _STMT), pkid)
    detail = verify_attestation_detailed(signed)
    assert detail["signatureValid"] is True
    assert detail["valid"] is False
    assert detail["keyStatus"] == "compromised"


def test_signed_before_compromise_still_valid(monkeypatch):
    priv, pub_hex, pkid = _key()
    _set_registry(monkeypatch, [{
        "publicKeyId": pkid, "publicKeyHex": pub_hex,
        "compromisedAt": "2026-12-01T00:00:00+00:00",  # after issued_at
    }])
    signed = SignedAttestation(_STMT, _sign(priv, _STMT), pkid)
    assert verify_attestation(signed) is True


def test_expired_key_rejected(monkeypatch):
    priv, pub_hex, pkid = _key()
    _set_registry(monkeypatch, [{
        "publicKeyId": pkid, "publicKeyHex": pub_hex,
        "validUntil": "2026-06-01T00:00:00+00:00",  # before issued_at
    }])
    signed = SignedAttestation(_STMT, _sign(priv, _STMT), pkid)
    detail = verify_attestation_detailed(signed)
    assert detail["valid"] is False
    assert detail["keyStatus"] == "expired"


def test_retired_key_rejected(monkeypatch):
    priv, pub_hex, pkid = _key()
    _set_registry(monkeypatch, [{
        "publicKeyId": pkid, "publicKeyHex": pub_hex, "status": "retired",
    }])
    signed = SignedAttestation(_STMT, _sign(priv, _STMT), pkid)
    assert verify_attestation(signed) is False


def test_tampered_signature_fails(monkeypatch):
    priv, pub_hex, pkid = _key()
    _set_registry(monkeypatch, [{"publicKeyId": pkid, "publicKeyHex": pub_hex}])
    signed = SignedAttestation(_STMT, "00" * 64, pkid)
    detail = verify_attestation_detailed(signed)
    assert detail["signatureValid"] is False
    assert detail["valid"] is False


def test_current_key_backward_compatible():
    """With no registry env, signing+verifying with the current key still works."""
    from app.core.attestation import build_attestation, sign_attestation
    stmt = build_attestation("review", "r1", {"x": 1}, workspace_id="ws")
    signed = sign_attestation(stmt)
    assert verify_attestation(signed) is True
