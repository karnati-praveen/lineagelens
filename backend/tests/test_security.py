import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.security import (
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
        ensure_workspace_scope(DummyAuth(), "ws-b")  # type: ignore[arg-type]
