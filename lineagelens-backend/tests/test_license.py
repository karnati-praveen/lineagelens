"""Tests for offline Ed25519 license verification (app.core.license)."""

from __future__ import annotations

import base64
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.core import license as lic


def _keypair() -> tuple[str, str]:
    """Return (private_seed_b64, public_key_hex) for an ephemeral vendor keypair."""
    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(seed).decode(), pub.hex()


# ── sign → verify roundtrip ───────────────────────────────────────────────────


def test_issue_and_verify_roundtrip() -> None:
    priv, pub = _keypair()
    key = lic.issue_license(plan="max", seats=25, customer="Acme", private_key_b64=priv)

    ent = lic.verify_license(key, public_key_hex=pub)

    assert ent.licensed is True
    assert ent.plan == "max"
    assert ent.seats == 25
    assert ent.customer == "Acme"
    assert ent.expires is None
    assert ent.allows("plus") and ent.allows("max")


def test_public_key_hex_for_matches_keypair() -> None:
    priv, pub = _keypair()
    assert lic.public_key_hex_for(priv) == pub


# ── rejection paths (each returns the free tier, never raises) ────────────────


def test_wrong_public_key_rejected() -> None:
    priv, _ = _keypair()
    _, other_pub = _keypair()
    key = lic.issue_license(plan="plus", private_key_b64=priv)

    ent = lic.verify_license(key, public_key_hex=other_pub)

    assert ent.licensed is False
    assert ent.plan == "lite"
    assert "signature" in ent.reason


def test_tampered_payload_rejected() -> None:
    priv, pub = _keypair()
    key = lic.issue_license(plan="plus", private_key_b64=priv)
    payload_b64, sig_b64 = key.split(".")
    # Flip the plan to max in the payload without re-signing.
    forged_payload = lic._b64url_encode(
        lic._b64url_decode(payload_b64).replace(b'"plan":"plus"', b'"plan":"max"')
    )
    forged = f"{forged_payload}.{sig_b64}"

    ent = lic.verify_license(forged, public_key_hex=pub)

    assert ent.licensed is False
    assert ent.plan == "lite"


def test_malformed_key_rejected() -> None:
    _, pub = _keypair()
    ent = lic.verify_license("not-a-valid-key", public_key_hex=pub)
    assert ent.licensed is False
    assert ent.reason == "malformed license key"


def test_placeholder_public_key_rejects_everything() -> None:
    priv, _ = _keypair()
    key = lic.issue_license(plan="max", private_key_b64=priv)

    ent = lic.verify_license(key, public_key_hex=lic._PLACEHOLDER_PUBLIC_KEY)

    assert ent.licensed is False
    assert "placeholder" in ent.reason


# ── expiry ────────────────────────────────────────────────────────────────────


def test_expired_license_degrades_to_free() -> None:
    priv, pub = _keypair()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    key = lic.issue_license(plan="max", expires=yesterday, private_key_b64=priv)

    ent = lic.verify_license(key, public_key_hex=pub)

    assert ent.licensed is False
    assert ent.plan == "lite"
    assert "expired" in ent.reason


def test_future_expiry_is_valid() -> None:
    priv, pub = _keypair()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    key = lic.issue_license(plan="plus", expires=tomorrow, private_key_b64=priv)

    ent = lic.verify_license(key, public_key_hex=pub)

    assert ent.licensed is True
    assert ent.plan == "plus"


def test_expiry_boundary_today_is_still_valid() -> None:
    priv, pub = _keypair()
    today = date(2026, 6, 16)
    key = lic.issue_license(plan="plus", expires=today.isoformat(), private_key_b64=priv)

    ent = lic.verify_license(key, public_key_hex=pub, today=today)

    assert ent.licensed is True


def test_issue_rejects_unknown_plan() -> None:
    priv, _ = _keypair()
    with pytest.raises(ValueError, match="plan must be one of"):
        lic.issue_license(plan="enterprise", private_key_b64=priv)


# ── plan ranking + effective_plan (topology ceiling) ──────────────────────────


def test_allows_respects_ranking() -> None:
    plus = lic.Entitlement(plan="plus", seats=0, customer="", expires=None, licensed=True)
    assert plus.allows("lite")
    assert plus.allows("plus")
    assert not plus.allows("max")


@pytest.mark.parametrize(
    ("plan", "backend_mode", "expected"),
    [
        ("max", "solo", "lite"),  # Max license on SQLite box capped to lite
        ("max", "team", "plus"),  # Max license on Postgres box capped to plus
        ("max", "enterprise", "max"),  # full topology unlocks max
        ("plus", "enterprise", "plus"),  # never grants more than licensed
        ("lite", "enterprise", "lite"),
    ],
)
def test_effective_plan_is_min_of_license_and_topology(plan, backend_mode, expected) -> None:
    ent = lic.Entitlement(plan=plan, seats=0, customer="", expires=None, licensed=True)
    assert lic.effective_plan(ent, backend_mode) == expected


# ── environment loading (LINEAGELENS_LICENSE / _FILE) ─────────────────────────


def test_load_entitlement_free_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("LINEAGELENS_LICENSE", raising=False)
    monkeypatch.delenv("LINEAGELENS_LICENSE_FILE", raising=False)
    lic.load_entitlement.cache_clear()

    ent = lic.load_entitlement()

    assert ent.licensed is False
    assert ent.plan == "lite"


def test_load_entitlement_from_env_key(monkeypatch) -> None:
    priv, pub = _keypair()
    key = lic.issue_license(plan="plus", customer="EnvCo", private_key_b64=priv)
    monkeypatch.setenv("LINEAGELENS_LICENSE_PUBLIC_KEY", pub)
    monkeypatch.setenv("LINEAGELENS_LICENSE", key)
    lic.load_entitlement.cache_clear()

    ent = lic.load_entitlement()

    assert ent.licensed is True
    assert ent.plan == "plus"
    assert ent.customer == "EnvCo"

    lic.load_entitlement.cache_clear()


def test_load_entitlement_from_file(monkeypatch, tmp_path) -> None:
    priv, pub = _keypair()
    key = lic.issue_license(plan="max", private_key_b64=priv)
    key_file = tmp_path / "license.key"
    key_file.write_text(key, encoding="utf-8")

    monkeypatch.delenv("LINEAGELENS_LICENSE", raising=False)
    monkeypatch.setenv("LINEAGELENS_LICENSE_PUBLIC_KEY", pub)
    monkeypatch.setenv("LINEAGELENS_LICENSE_FILE", str(key_file))
    lic.load_entitlement.cache_clear()

    ent = lic.load_entitlement()

    assert ent.licensed is True
    assert ent.plan == "max"

    lic.load_entitlement.cache_clear()
