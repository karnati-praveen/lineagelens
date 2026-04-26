import os
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/provenance"
DEFAULT_DB_POOL_SIZE = 10
DEFAULT_DB_MAX_OVERFLOW = 20
DEFAULT_DB_POOL_TIMEOUT_SECONDS = 30
DEFAULT_DB_POOL_RECYCLE_SECONDS = 1800


def _get_env_value(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _get_int_env_value(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default

    try:
        return int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error


def create_engine_from_env() -> AsyncEngine:
    return create_async_engine(
        _get_env_value("DATABASE_URL", DEFAULT_DATABASE_URL),
        pool_pre_ping=True,
        pool_size=_get_int_env_value("DB_POOL_SIZE", DEFAULT_DB_POOL_SIZE),
        max_overflow=_get_int_env_value("DB_MAX_OVERFLOW", DEFAULT_DB_MAX_OVERFLOW),
        pool_timeout=_get_int_env_value("DB_POOL_TIMEOUT_SECONDS", DEFAULT_DB_POOL_TIMEOUT_SECONDS),
        pool_recycle=_get_int_env_value("DB_POOL_RECYCLE_SECONDS", DEFAULT_DB_POOL_RECYCLE_SECONDS),
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def get_session_factory_from_app(app: Any) -> async_sessionmaker[AsyncSession]:
    session_factory = getattr(app.state, "db_session_factory", None)
    if not isinstance(session_factory, async_sessionmaker):
        raise RuntimeError("Database session factory is not available on application state.")
    return session_factory


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return get_session_factory_from_app(request.app)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    session_factory = get_session_factory(request)
    async with session_factory() as session:
        yield session


async def initialize_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT 1"))

        migration_table = await connection.execute(
            text("SELECT to_regclass('public.alembic_version')")
        )
        has_migration_table = migration_table.scalar_one_or_none() is not None

        if not has_migration_table:
            raise RuntimeError(
                "Database migrations have not been applied. "
                "Run 'alembic upgrade head' before starting the API."
            )

        revision = await connection.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        if not revision.scalar_one_or_none():
            raise RuntimeError(
                "Database migration history is empty. "
                "Run 'alembic upgrade head' before starting the API."
            )

        role_check = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'user_accounts' "
                "AND column_name = 'role'"
            )
        )
        if role_check.scalar_one_or_none() is None:
            raise RuntimeError(
                "Database schema is out of date: 'role' column is missing from "
                "user_accounts. Run 'alembic upgrade head' to apply all pending migrations."
            )

        token_version_check = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'user_accounts' "
                "AND column_name = 'token_version'"
            )
        )
        if token_version_check.scalar_one_or_none() is None:
            raise RuntimeError(
                "Database schema is out of date: 'token_version' column is missing from "
                "user_accounts. Run 'alembic upgrade head' to apply all pending migrations."
            )
