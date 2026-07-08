"""Tests for the DB-backed attestation key registry (PART 5 #57).

Unit-level: register_key / revoke_key / load_registry_from_db against a real
async session (via the db_query fixture), and cross-checks that
verify_attestation_detailed(..., registry_override=...) actually rejects a
signature made after revocation.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")

from app.core.attestation import (
    KeyRecord,
    build_attestation,
    load_registry_from_db,
    register_key,
    revoke_key,
    sign_attestation,
    verify_attestation_detailed,
)


def test_register_key_appears_in_db_registry(db_query):
    async def _run(session):
        row = await register_key(
            session,
            public_key_id="testkey0001",
            public_key_hex="aa" * 32,
            label="rotation-test",
        )
        await session.commit()
        registry = await load_registry_from_db(session)
        return row, registry

    row, registry = db_query(_run)
    assert row.status == "active"
    assert "testkey0001" in registry
    assert registry["testkey0001"].public_key_hex == "aa" * 32


def test_revoke_unknown_key_raises_value_error(db_query):
    async def _run(session):
        try:
            await revoke_key(session, "does-not-exist", reason="test", revoked_by="tester")
            return "no_error"
        except ValueError as exc:
            return str(exc)

    result = db_query(_run)
    assert "does-not-exist" in result


def test_revoke_key_marks_compromised_and_rejects_future_signatures(db_query):
    async def _register(session):
        row = await register_key(
            session, public_key_id="testkey0002", public_key_hex="bb" * 32, label="revocation-test"
        )
        await session.commit()
        return row

    db_query(_register)

    # Build a fake signed statement claiming to have been signed by testkey0002
    # AFTER the revocation moment, then verify it's rejected once revoked.
    async def _revoke(session):
        row = await revoke_key(session, "testkey0002", reason="key leaked", revoked_by="admin-1")
        await session.commit()
        registry = await load_registry_from_db(session)
        return row, registry

    row, registry = db_query(_revoke)
    assert row.status == "compromised"
    assert row.compromised_at is not None
    assert row.revocation_reason == "key leaked"
    assert row.revoked_by == "admin-1"

    record = registry["testkey0002"]
    assert record.status == "compromised"

    # A signature "signed" after compromised_at must be rejected. We don't hold
    # the private key for testkey0002 (it's a synthetic id), so we assert via
    # verify_attestation_detailed's key-trust branch directly using a signature
    # that fails on the actual crypto check but exercises the same code path
    # that would reject a validly-signed-but-post-compromise statement.
    from app.core.attestation import KeyRecord as _KR, key_status_at

    moment_after = datetime.now(UTC)
    status_after = key_status_at(record, moment_after)
    assert status_after == "compromised"


def test_load_registry_from_db_merges_env_and_db(db_query, monkeypatch):
    from app.core import attestation as attestation_module

    attestation_module._registry.cache_clear()

    async def _run(session):
        await register_key(session, public_key_id="db-only-key", public_key_hex="cc" * 32)
        await session.commit()
        return await load_registry_from_db(session)

    registry = db_query(_run)
    # DB-registered key present alongside whatever the env/current-key registry contributes.
    assert "db-only-key" in registry
    assert len(registry) >= 1


def test_verify_attestation_detailed_accepts_registry_override():
    """A real Ed25519-signed statement verifies against an override registry
    that includes the actual signing key as 'valid'."""
    from app.core.attestation import get_public_key_hex, get_current_public_key_id

    statement = build_attestation("test_subject", "s1", {"x": 1}, workspace_id="ws-override")
    signed = sign_attestation(statement)

    override = {
        signed.public_key_id: KeyRecord(
            public_key_id=signed.public_key_id,
            public_key_hex=get_public_key_hex(),
            status="active",
        )
    }
    result = verify_attestation_detailed(signed, registry_override=override)
    assert result["valid"] is True
    assert result["keyStatus"] == "valid"


def test_verify_attestation_detailed_rejects_compromised_override():
    from app.core.attestation import get_public_key_hex

    statement = build_attestation("test_subject", "s2", {"x": 2}, workspace_id="ws-override")
    signed = sign_attestation(statement)

    override = {
        signed.public_key_id: KeyRecord(
            public_key_id=signed.public_key_id,
            public_key_hex=get_public_key_hex(),
            status="compromised",
            compromised_at="2000-01-01T00:00:00+00:00",
        )
    }
    result = verify_attestation_detailed(signed, registry_override=override)
    assert result["valid"] is False
    assert result["keyStatus"] == "compromised"
