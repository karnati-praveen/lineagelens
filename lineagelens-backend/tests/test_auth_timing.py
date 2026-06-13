"""Tests for L1: login timing side-channel mitigation.

Verifies that verify_password is always called — even when the username is not
found — so that "unknown user" and "wrong password" paths consume the same time.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.api.routes.auth import _DUMMY_HASH, login_user, token_login
from app.core.security import verify_password


# ── _DUMMY_HASH sanity checks ─────────────────────────────────────────────────

def test_dummy_hash_has_expected_format() -> None:
    parts = _DUMMY_HASH.split("$")
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) >= 390000  # at least the old iteration count
    assert len(parts[2]) >= 16      # salt b64 present
    assert len(parts[3]) >= 32      # digest b64 present


def test_dummy_hash_never_matches_any_password() -> None:
    for pw in ("", "password", "Str0ng!Pass123", "x" * 1000):
        assert not verify_password(pw, _DUMMY_HASH), f"dummy hash matched: {pw!r}"


def test_dummy_hash_is_parseable_by_verify_password_runs_kdf() -> None:
    # verify_password must not raise — it must return False gracefully.
    result = verify_password("arbitrary", _DUMMY_HASH)
    assert result is False


# ── verify_password called even when user is not found ────────────────────────

@pytest.mark.asyncio
async def test_token_login_calls_verify_password_for_unknown_user() -> None:
    """verify_password must be invoked even when the username lookup returns None."""
    form = MagicMock()
    form.username = "nobody@example.com"
    form.password = "WrongPass1!"

    session = MagicMock()
    # Simulate: user not found
    session.execute = MagicMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )

    settings = MagicMock()
    settings.rate_limit_enabled = False

    with patch(
        "app.api.routes.auth.get_user_by_username", return_value=None
    ) as mock_lookup, patch(
        "app.api.routes.auth.verify_password", return_value=False
    ) as mock_verify:
        with pytest.raises(Exception):  # HTTPException 401
            await token_login(form, session, settings)

    mock_lookup.assert_called_once()
    mock_verify.assert_called_once()
    # The dummy hash (not a real user hash) must have been passed
    _call_hash_arg = mock_verify.call_args[0][1]
    assert _call_hash_arg == _DUMMY_HASH


@pytest.mark.asyncio
async def test_login_user_calls_verify_password_for_unknown_user() -> None:
    """Same guarantee for the JSON login endpoint."""
    from app.schemas.auth import LoginRequest

    payload = LoginRequest(username="nobody", password="WrongPass1!")
    session = MagicMock()
    settings = MagicMock()
    settings.rate_limit_enabled = False

    with patch(
        "app.api.routes.auth.get_user_by_username", return_value=None
    ) as mock_lookup, patch(
        "app.api.routes.auth.verify_password", return_value=False
    ) as mock_verify:
        with pytest.raises(Exception):  # HTTPException 401
            await login_user(payload, session, settings)

    mock_lookup.assert_called_once()
    mock_verify.assert_called_once()
    _call_hash_arg = mock_verify.call_args[0][1]
    assert _call_hash_arg == _DUMMY_HASH
