"""Tests for the generic customer-countersignature verify helper (PART 5 #57).

This does NOT manage customer keys (no KMS/HSM integration — scoped follow-up,
see SECURITY_NOTES.md). It only verifies a signature against a customer-
supplied public key, standing in for a "customer root" that countersigns a
LineageLens statement using a key held entirely out of band.
"""
from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.attestation import verify_customer_countersignature


def _customer_keypair():
    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return private_key, public_hex


def test_valid_countersignature_verifies():
    private_key, public_hex = _customer_keypair()
    payload = b'{"claim":"coverage=100%"}'
    signature_hex = private_key.sign(payload).hex()

    assert verify_customer_countersignature(payload, public_hex, signature_hex) is True


def test_tampered_payload_fails_verification():
    private_key, public_hex = _customer_keypair()
    payload = b'{"claim":"coverage=100%"}'
    signature_hex = private_key.sign(payload).hex()

    tampered_payload = b'{"claim":"coverage=0%"}'
    assert verify_customer_countersignature(tampered_payload, public_hex, signature_hex) is False


def test_wrong_key_fails_verification():
    private_key, _ = _customer_keypair()
    _, other_public_hex = _customer_keypair()
    payload = b"same payload"
    signature_hex = private_key.sign(payload).hex()

    assert verify_customer_countersignature(payload, other_public_hex, signature_hex) is False


def test_malformed_inputs_never_raise():
    assert verify_customer_countersignature(b"x", "not-hex", "also-not-hex") is False
    assert verify_customer_countersignature(b"x", "", "") is False
