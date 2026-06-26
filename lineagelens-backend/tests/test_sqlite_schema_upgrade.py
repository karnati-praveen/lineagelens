import asyncio
import tempfile
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import initialize_database


LEGACY_PROVENANCE_SCHEMA_SQL = """
CREATE TABLE provenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    workspace_id TEXT NOT NULL,
    user_id TEXT,
    request_uuid TEXT,
    file_path TEXT NOT NULL,
    file_uri TEXT,
    cursor_line INTEGER,
    cursor_column INTEGER,
    timestamp_iso TEXT NOT NULL,
    prompt_messages JSON,
    model_name TEXT,
    model_parameters JSON,
    raw_model_response TEXT,
    inserted_code TEXT NOT NULL,
    surrounding_context JSON,
    context_snapshot JSON,
    embeddings JSON,
    ast_snapshot JSON,
    embedding_vector JSON,
    embedding_model TEXT,
    lineage_node_id TEXT,
    provenance_payload JSON NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


async def _upgrade_legacy_sqlite_database(db_url: str) -> tuple[list[str], list[str]]:
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(LEGACY_PROVENANCE_SCHEMA_SQL)

        await initialize_database(engine)

        async with engine.begin() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: [
                    column["name"] for column in inspect(sync_connection).get_columns("provenance_records")
                ]
            )
            indexes = await connection.run_sync(
                lambda sync_connection: [
                    index["name"] for index in inspect(sync_connection).get_indexes("provenance_records")
                ]
            )

        return columns, indexes
    finally:
        await engine.dispose()


def test_initialize_database_upgrades_legacy_sqlite_provenance_schema() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_url = f"sqlite+aiosqlite:///{Path(temp_dir) / 'legacy.db'}"
        columns, indexes = asyncio.run(_upgrade_legacy_sqlite_database(db_url))

    assert "risk_score" in columns
    assert "token_count" in columns
    assert "cost_usd" in columns
    assert "is_redacted" in columns
    # PART 2 #10/#11 — content commitments + privacy lifecycle state.
    assert "prompt_sha256" in columns
    assert "content_sha256" in columns
    assert "lifecycle_state" in columns
    assert "ix_provenance_records_risk_score" in indexes
    assert "ix_provenance_workspace_risk" in indexes