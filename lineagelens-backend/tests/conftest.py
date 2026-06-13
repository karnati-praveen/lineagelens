import os


os.environ["APP_ENV"] = "test"
os.environ["BACKEND_MODE"] = "team"
os.environ["NEO4J_ENABLED"] = "false"
os.environ["VECTOR_SEARCH_ENABLED"] = "false"
os.environ["LINEAGE_STRICT_MODE"] = "false"
os.environ["BACKEND_CORS_ORIGINS"] = "http://localhost:3000"
os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789"
os.environ["JWT_REFRESH_SECRET_KEY"] = "pytest-refresh-secret-key-0123456789abcdefghijklmnopqrstuvwxyz"


# ─────────────────────────────────────────────────────────────────────────────
# API-level test harness (shared by the route contract tests).
#
# NOTE on the fixture pattern: the repo had NO pre-existing HTTP/TestClient
# fixtures — `conftest.py` only set environment variables and every existing
# test was a pure-function unit test.  The task brief assumed an app/auth/SQLite
# harness already existed to "mirror"; it did not, so the harness below is new.
# It is additive — existing unit tests do not request these fixtures and are
# unaffected.
#
# How it works:
#   * Each test gets an isolated file-backed SQLite DB under tmp_path.
#   * DATABASE_URL is monkeypatched and the Settings lru_cache is cleared so the
#     app's lifespan builds its engine against that DB.
#   * Users are seeded directly into the same DB file via a short-lived engine
#     run on its own event loop (asyncio.run), then JWT access tokens are minted
#     with app.core.security.create_access_token — exactly the tokens the real
#     login flow issues (matching scopes, audience, issuer, token_version).
# ─────────────────────────────────────────────────────────────────────────────

import uuid as _uuid
from dataclasses import dataclass

import pytest


_DEFAULT_TEST_PASSWORD = "Sup3rSecretTestPw!1"


def _engine_for(database_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    return create_async_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )


def _seed_users(database_url: str, users: list[dict]) -> None:
    """Create the schema (idempotent) plus the given workspaces and user rows.

    Runs on a private event loop with its own engine that is disposed before
    returning, so it never shares a loop or connection with the app under test.
    """
    import asyncio

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.core.security import hash_password
        from app.db.base import Base
        from app.db.models import UserAccount, Workspace

        engine = _engine_for(database_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                seen_ws: set[str] = set()
                for user in users:
                    workspace_id = user["workspace_id"]
                    if workspace_id in seen_ws:
                        continue
                    seen_ws.add(workspace_id)
                    if await session.get(Workspace, workspace_id) is None:
                        session.add(Workspace(id=workspace_id, name=workspace_id))
                for user in users:
                    session.add(
                        UserAccount(
                            id=_uuid.UUID(user["id"]),
                            username=user["username"],
                            password_hash=hash_password(_DEFAULT_TEST_PASSWORD),
                            workspace_id=user["workspace_id"],
                            role=user["role"],
                            is_active=user.get("is_active", True),
                            token_version=0,
                        )
                    )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


@dataclass
class SeededUser:
    id: str
    username: str
    role: str
    workspace_id: str
    token: str

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient bound to a fresh, isolated SQLite database for one test."""
    db_file = tmp_path / "api_test.db"
    database_url = f"sqlite+aiosqlite:///{db_file}"

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    # Lower the body cap to its minimum so the 413 oversized-body test can send a
    # ~9 KB body instead of >2 MB. Still well above any payload these tests post.
    monkeypatch.setenv("HTTP_MAX_BODY_BYTES", "8192")

    from app.core.config import get_settings

    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        test_client.database_url = database_url  # type: ignore[attr-defined]
        # Seed a throwaway admin so the SetupGuard middleware treats setup as
        # complete (it redirects every request to /setup until a user exists).
        _seed_users(
            database_url,
            [
                {
                    "id": str(_uuid.uuid4()),
                    "username": f"setup-admin-{_uuid.uuid4().hex[:8]}",
                    "role": "admin",
                    "workspace_id": f"setup-ws-{_uuid.uuid4().hex[:8]}",
                }
            ],
        )
        yield test_client

    get_settings.cache_clear()


@pytest.fixture()
def make_user(client):
    """Factory: seed a user with a given role/workspace and return it with a token."""

    def _make(role: str = "member", workspace_id: str | None = None, username: str | None = None) -> SeededUser:
        from app.core.config import get_settings
        from app.core.security import create_access_token

        settings = get_settings()
        user_id = str(_uuid.uuid4())
        workspace = workspace_id or f"ws-{_uuid.uuid4().hex[:8]}"
        name = username or f"user-{user_id[:8]}"

        _seed_users(
            client.database_url,
            [{"id": user_id, "username": name, "role": role, "workspace_id": workspace}],
        )

        token, _ = create_access_token(
            subject=user_id,
            workspace_id=workspace,
            scopes=sorted(settings.required_scopes_set),
            settings=settings,
            extra_claims={"token_version": 0, "role": role},
        )
        return SeededUser(id=user_id, username=name, role=role, workspace_id=workspace, token=token)

    return _make


@pytest.fixture()
def db_query(client):
    """Run a coroutine ``async fn(session)`` against the test DB and return its result.

    Used by tests to assert on persisted side effects (audit rows, record counts)
    without going through the API. Uses a short-lived engine on its own loop.
    """
    import asyncio

    def _run(async_fn):
        async def _wrap():
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

            engine = _engine_for(client.database_url)
            try:
                factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    return await async_fn(session)
            finally:
                await engine.dispose()

        return asyncio.run(_wrap())

    return _run
