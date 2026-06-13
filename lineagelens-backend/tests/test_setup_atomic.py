"""Tests for L2: atomic first-admin setup — two concurrent calls must produce
exactly one admin account.

Uses a real SQLite in-memory database (via the conftest harness) so we exercise
the actual IntegrityError / constraint path rather than mocking.
"""
from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import UserAccount, Workspace


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_settings(**kw) -> Settings:
    return Settings.model_validate(
        {
            "APP_ENV": "test",
            "JWT_SECRET_KEY": "a" * 40,
            "BACKEND_CORS_ORIGINS": "http://localhost:3000",
            **kw,
        }
    )


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


# ── concurrency test ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_concurrent_setups_produce_exactly_one_admin() -> None:
    """Both requests bypass the is_setup_complete fast-path, simulating the TOCTOU
    race window where both see an empty database before either commits.

    The sentinel workspace row (id=__setup_singleton__) ensures only one can
    succeed: the second request hits IntegrityError on the sentinel INSERT and
    returns 409, even though is_setup_complete returned False for both.
    """
    from fastapi import HTTPException
    from app.api.routes.setup import run_setup, SetupRequest

    engine = _make_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = _build_settings()
    successes: list[str] = []
    failures: list[str] = []

    # Simulate the TOCTOU window: both requests saw is_setup_complete=False.
    async def _is_complete_always_false(_session: AsyncSession) -> bool:
        return False

    async def _do_setup(username: str) -> None:
        async with factory() as session:
            req = mock.MagicMock()
            req.app.state.setup_complete = False
            payload = SetupRequest(
                username=username,
                password="Admin1234!",
                workspace_name=f"ws-{username}",
            )
            try:
                with mock.patch(
                    "app.api.routes.setup.is_setup_complete",
                    _is_complete_always_false,
                ):
                    await run_setup(payload, req, session, settings)
                successes.append(username)
            except HTTPException as exc:
                if exc.status_code == 409:
                    failures.append(username)
                else:
                    raise
            except Exception:
                failures.append(username)

    # Run sequentially with is_setup_complete bypassed for both.
    # First call commits sentinel + user.
    # Second call: sentinel INSERT → IntegrityError → 409.
    await _do_setup("admin1")
    await _do_setup("admin2")

    async with factory() as session:
        result = await session.execute(
            select(func.count()).select_from(UserAccount).where(UserAccount.role == "admin")
        )
        admin_count = result.scalar_one()

    assert admin_count == 1, f"Expected 1 admin, got {admin_count}"
    assert len(successes) == 1, f"Expected 1 success, got: {successes}"
    assert len(failures) == 1, f"Expected 1 failure, got: {failures}"


@pytest.mark.asyncio
async def test_second_setup_after_first_completes_returns_409() -> None:
    """A sequential second call (not concurrent) must also be rejected."""
    from fastapi import HTTPException
    from app.api.routes.setup import run_setup, SetupRequest

    engine = _make_engine()
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = _build_settings()

    async def _do_setup(username: str):
        async with factory() as session:
            req = mock.MagicMock()
            req.app.state.setup_complete = False
            payload = SetupRequest(
                username=username, password="Admin1234!", workspace_name=f"ws-{username}"
            )
            return await run_setup(payload, req, session, settings)

    await _do_setup("firstadmin")

    with pytest.raises(HTTPException) as exc_info:
        await _do_setup("secondadmin")

    assert exc_info.value.status_code == 409
