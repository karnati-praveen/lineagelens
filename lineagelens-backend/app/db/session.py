from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
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
