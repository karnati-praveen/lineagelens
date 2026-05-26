from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings

logger = logging.getLogger(__name__)

_CURRENT_ALEMBIC_HEAD = "202501240001"
_SQLITE_PROVENANCE_COLUMNS = {
    "risk_score": "INTEGER",
    "token_count": "INTEGER",
    "cost_usd": "FLOAT",
    "is_redacted": "BOOLEAN NOT NULL DEFAULT 0",
}
_SQLITE_PROVENANCE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_provenance_records_risk_score ON provenance_records (risk_score)",
    "CREATE INDEX IF NOT EXISTS ix_provenance_workspace_risk ON provenance_records (workspace_id, risk_score)",
)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    if _is_sqlite(settings.database_url):
        from sqlalchemy.pool import StaticPool
        return create_async_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
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


def _upgrade_sqlite_schema(connection) -> None:
    inspector = inspect(connection)
    if "provenance_records" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("provenance_records")}
    for column_name, column_sql in _SQLITE_PROVENANCE_COLUMNS.items():
        if column_name not in existing_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE provenance_records ADD COLUMN {column_name} {column_sql}"
            )

    for statement in _SQLITE_PROVENANCE_INDEXES:
        connection.exec_driver_sql(statement)


async def initialize_database(engine: AsyncEngine) -> None:
    url_str = str(engine.url)

    if _is_sqlite(url_str):
        # SQLite lite mode: auto-create all tables, no migrations needed
        from app.db.base import Base
        import os
        # Ensure the data directory exists for file-based SQLite
        db_path = url_str.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        if db_path and not db_path.startswith(":"):
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_upgrade_sqlite_schema)
        logger.info("SQLite database initialised (create_all + schema upgrade).")
        return

    # Postgres: verify migrations have been applied
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
        current_revision = revision.scalar_one_or_none()
        if current_revision != _CURRENT_ALEMBIC_HEAD:
            raise RuntimeError(
                f"Database schema is not at Alembic head ({_CURRENT_ALEMBIC_HEAD}). "
                "Run 'alembic upgrade head' before starting the API."
            )

        provenance_columns = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'provenance_records' "
                "AND column_name IN ('risk_score', 'token_count', 'cost_usd', 'is_redacted')"
            )
        )
        present_columns = set(provenance_columns.scalars().all())
        missing_columns = {"risk_score", "token_count", "cost_usd", "is_redacted"} - present_columns
        if missing_columns:
            missing_list = ", ".join(sorted(missing_columns))
            raise RuntimeError(
                "Database schema is out of date: 'provenance_records' is missing columns "
                f"{missing_list}. Run 'alembic upgrade head'."
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
                "Database schema is out of date: 'role' column missing. "
                "Run 'alembic upgrade head'."
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
                "Database schema is out of date: 'token_version' column missing. "
                "Run 'alembic upgrade head'."
            )

        jti_check = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'user_accounts' "
                "AND column_name = 'refresh_token_jti'"
            )
        )
        if jti_check.scalar_one_or_none() is None:
            raise RuntimeError(
                "Database schema is out of date: 'refresh_token_jti' column missing. "
                "Run 'alembic upgrade head'."
            )

        workspaces_check = await connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name = 'workspaces'"
            )
        )
        if workspaces_check.scalar_one_or_none() is None:
            raise RuntimeError(
                "Database schema is out of date: 'workspaces' table missing. "
                "Run 'alembic upgrade head'."
            )

        vector_ext = await connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        if vector_ext.scalar_one_or_none() is None:
            logger.warning(
                "pgvector extension is not installed. Vector search will not work. "
                "Install with: CREATE EXTENSION vector;"
            )
