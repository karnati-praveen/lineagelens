from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


settings = get_settings()


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
    pool_recycle=settings.db_pool_recycle_seconds,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def initialize_database() -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT 1"))
        migration_table = await connection.execute(
            text("SELECT to_regclass('public.alembic_version')")
        )
        has_migration_table = migration_table.scalar_one_or_none() is not None

        if not has_migration_table:
            raise RuntimeError(
                "Database migrations have not been applied. Run 'alembic upgrade head' before starting the API."
            )

        revision = await connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        if not revision.scalar_one_or_none():
            raise RuntimeError(
                "Database migration history is empty. Run 'alembic upgrade head' before starting the API."
            )
