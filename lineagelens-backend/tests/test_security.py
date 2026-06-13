from typing import cast

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.security import (
    AuthContext,
    AuthError,
    create_access_token,
    decode_token,
    ensure_workspace_scope,
    hash_password,
    verify_password,
)


BASE_SETTINGS = {
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "b" * 40,
    "BACKEND_CORS_ORIGINS": "http://localhost:3000",
}


def build_settings(**overrides: object) -> Settings:
    payload = {**BASE_SETTINGS, **overrides}
    return Settings.model_validate(payload)


def test_access_token_round_trip() -> None:
    settings = build_settings()

    token, _ = create_access_token(
        subject="user-123",
        workspace_id="ws-test",
        scopes=["provenance:read", "provenance:write"],
        settings=settings,
    )

    auth = decode_token(token, settings)

    assert auth.subject == "user-123"
    assert auth.workspace_id == "ws-test"
    assert auth.token_type == "access"


def test_decode_token_rejects_missing_required_scopes() -> None:
    settings = build_settings()

    token, _ = create_access_token(
        subject="user-123",
        workspace_id="ws-test",
        scopes=["provenance:read"],
        settings=settings,
    )

    with pytest.raises(AuthError, match="missing required scope"):
        decode_token(token, settings, require_scopes=True)


def test_password_hash_and_verify() -> None:
    password = "Str0ngPassword!123"

    digest = hash_password(password)

    assert verify_password(password, digest)
    assert not verify_password("wrong-password", digest)


def test_ensure_workspace_scope_rejects_mismatch() -> None:
    class DummyAuth:
        workspace_id = "ws-a"

    with pytest.raises(HTTPException):
        ensure_workspace_scope(cast(AuthContext, DummyAuth()), "ws-b")


# ── L3: PBKDF2 iteration count ────────────────────────────────────────────────

def test_pbkdf2_iterations_at_least_600k() -> None:
    from app.core.security import _PBKDF2_ITERATIONS
    assert _PBKDF2_ITERATIONS >= 600_000


def test_old_hash_at_390k_still_verifies_after_bump() -> None:
    """A hash produced at 390k iterations must still pass verify_password.

    verify_password reads the iteration count from the stored hash string, so
    bumping _PBKDF2_ITERATIONS only affects new hashes, not existing ones.
    """
    import base64, hashlib, os as _os

    password = "Str0ngPassword!123"
    old_iterations = 390_000

    salt = _os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, old_iterations)
    old_hash = "$".join([
        "pbkdf2_sha256",
        str(old_iterations),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ])

    assert verify_password(password, old_hash), "old hash must still verify"
    assert not verify_password("wrong", old_hash)


def test_new_hash_uses_600k_iterations() -> None:
    """hash_password must now produce hashes with 600k iterations."""
    new_hash = hash_password("TestPass1!")
    parts = new_hash.split("$")
    assert int(parts[1]) == 600_000
