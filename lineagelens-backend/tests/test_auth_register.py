"""Tests for /auth/register helpers and validation logic.

All tests are pure-function — no DB, no HTTP server.

Run with:
    cd lineagelens-backend && pytest tests/test_auth_register.py -v
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.routes.auth import (
    _registration_conflict_detail,
    create_default_workspace_id,
    normalize_username,
    normalize_workspace_id,
    validate_password_strength,
)
from app.core.config import Settings


# ── helpers ───────────────────────────────────────────────────────────────────

BASE = {
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "a" * 40,
    "BACKEND_CORS_ORIGINS": "http://localhost:3000",
}


def build_settings(**overrides: object) -> Settings:
    return Settings.model_validate({**BASE, **overrides})


def _make_integrity_error(orig_msg: str) -> IntegrityError:
    """Construct a minimal IntegrityError whose .orig prints the given message."""
    class _FakeOrig:
        def __str__(self) -> str:
            return orig_msg

    err = IntegrityError("stmt", {}, Exception(orig_msg))
    err.orig = _FakeOrig()  # type: ignore[assignment]
    return err


# ── _registration_conflict_detail ────────────────────────────────────────────

def test_conflict_detail_reports_username_for_user_account_table() -> None:
    err = _make_integrity_error("UNIQUE constraint failed: user_accounts.username")
    assert _registration_conflict_detail(err) == "Username already taken."


def test_conflict_detail_reports_username_keyword() -> None:
    err = _make_integrity_error("duplicate key value violates unique constraint (username)")
    assert _registration_conflict_detail(err) == "Username already taken."


def test_conflict_detail_reports_username_for_uq_user_prefix() -> None:
    err = _make_integrity_error("uq_user_accounts_username duplicate")
    assert _registration_conflict_detail(err) == "Username already taken."


def test_conflict_detail_defaults_to_workspace_message() -> None:
    err = _make_integrity_error("UNIQUE constraint failed: workspaces.id")
    assert _registration_conflict_detail(err) == "Workspace ID already taken."


def test_conflict_detail_generic_error_returns_workspace_message() -> None:
    err = _make_integrity_error("some other constraint violation")
    assert _registration_conflict_detail(err) == "Workspace ID already taken."


# ── normalize_username ────────────────────────────────────────────────────────

def test_normalize_username_lowercases_and_strips() -> None:
    assert normalize_username("  Alice  ") == "alice"


def test_normalize_username_rejects_empty() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_username("   ")
    assert exc_info.value.status_code == 400


def test_normalize_username_rejects_too_long() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_username("a" * 129)
    assert exc_info.value.status_code == 400


def test_normalize_username_rejects_invalid_chars() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_username("alice!")  # exclamation not allowed
    assert exc_info.value.status_code == 400


def test_normalize_username_allows_dots_dashes_underscores_at() -> None:
    assert normalize_username("alice.bob-c_d@example") == "alice.bob-c_d@example"


# ── validate_password_strength ────────────────────────────────────────────────

def test_validate_password_strength_accepts_strong_password() -> None:
    settings = build_settings()
    validate_password_strength("StrongPass1!", settings)  # no exception


def test_validate_password_strength_rejects_short_password() -> None:
    settings = build_settings(AUTH_PASSWORD_MIN_LENGTH=12)
    with pytest.raises(HTTPException) as exc_info:
        validate_password_strength("short", settings)
    assert exc_info.value.status_code == 400
    assert "12" in exc_info.value.detail


def test_validate_password_strength_minimum_is_at_least_8() -> None:
    # AUTH_PASSWORD_MIN_LENGTH cannot be set below 8 (pydantic validator enforces it).
    # Use the default (8) and verify that a 7-character password is still rejected.
    settings = build_settings()
    with pytest.raises(HTTPException):
        validate_password_strength("1234567", settings)   # 7 chars — below floor of 8


# ── create_default_workspace_id ──────────────────────────────────────────────

def test_create_default_workspace_id_starts_with_ws() -> None:
    ws = create_default_workspace_id("alice")
    assert ws.startswith("ws-alice-")


def test_create_default_workspace_id_is_unique() -> None:
    a = create_default_workspace_id("alice")
    b = create_default_workspace_id("alice")
    assert a != b


def test_create_default_workspace_id_handles_special_chars_in_username() -> None:
    ws = create_default_workspace_id("alice.bob@example")
    assert ws.startswith("ws-")
    # slug part should only contain alphanumeric and hyphens
    slug = ws.removeprefix("ws-").rsplit("-", 1)[0]
    assert all(c.isalnum() or c == "-" for c in slug)


def test_create_default_workspace_id_handles_empty_username() -> None:
    ws = create_default_workspace_id("")
    assert ws.startswith("ws-workspace-")


# ── normalize_workspace_id ───────────────────────────────────────────────────

def test_normalize_workspace_id_returns_none_for_none() -> None:
    assert normalize_workspace_id(None) is None


def test_normalize_workspace_id_strips_whitespace() -> None:
    assert normalize_workspace_id("  ws-alice  ") == "ws-alice"


def test_normalize_workspace_id_returns_none_for_blank() -> None:
    assert normalize_workspace_id("   ") is None


def test_normalize_workspace_id_rejects_invalid_chars() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_workspace_id("ws alice!")
    assert exc_info.value.status_code == 400


def test_normalize_workspace_id_allows_colons_and_dots() -> None:
    result = normalize_workspace_id("org:team.project-1")
    assert result == "org:team.project-1"


# ── REGISTRATION_ENABLED setting ─────────────────────────────────────────────

def test_registration_enabled_is_true_by_default() -> None:
    settings = build_settings()
    assert settings.registration_enabled is True


def test_registration_can_be_disabled_via_env_var() -> None:
    settings = build_settings(REGISTRATION_ENABLED=False)
    assert settings.registration_enabled is False
