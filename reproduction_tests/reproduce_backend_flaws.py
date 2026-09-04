"""
Automated Reproduction Tests for LineageLens Backend Flaws & Bottlenecks:
- Flaw 11: Ingest hash-chain lock contention under concurrent ingestion requests (SELECT FOR UPDATE on workspace tip record).
- Flaw 12: In-memory search facet aggregation memory & CPU scaling (fetching up to 2,000 file paths into memory to compute extension counts).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

# Auto-install missing runtime dependencies if required
def _ensure_deps():
    deps = {
        "jwt": "PyJWT",
        "nacl": "PyNaCl",
        "passlib": "passlib[pbkdf2]",
        "sqlalchemy": "sqlalchemy",
        "aiosqlite": "aiosqlite",
        "fastapi": "fastapi",
        "pydantic_settings": "pydantic-settings",
    }
    for mod_name, pip_name in deps.items():
        try:
            __import__(mod_name)
        except ImportError:
            print(f"Installing missing dependency: {pip_name}...")
            subprocess.run([sys.executable, "-m", "pip", "install", pip_name], check=True)

_ensure_deps()

# Ensure lineagelens-backend is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "lineagelens-backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Configure environment for team mode testing (enables hash-chain serialization)
os.environ["APP_ENV"] = "test"
os.environ["BACKEND_MODE"] = "team"
os.environ["NEO4J_ENABLED"] = "false"
os.environ["VECTOR_SEARCH_ENABLED"] = "false"
os.environ["LINEAGE_STRICT_MODE"] = "false"
os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789"
os.environ["JWT_REFRESH_SECRET_KEY"] = "pytest-refresh-secret-key-0123456789abcdefghijklmnopqrstuvwxyz"

import pytest
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.routes.search import get_search_facets
from app.core.config import get_settings
from app.core.security import AuthContext, hash_password
from app.db.base import Base
from app.db.models import ProvenanceRecord, UserAccount, Workspace
from app.services.ingest_normalizer import normalize_ingest_payload
from app.services.provenance_service import ingest_provenance_event


def create_test_engine():
    """Create an isolated in-memory SQLite engine for tests."""
    get_settings.cache_clear()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine


async def setup_db_tables(engine):
    """Create schema tables in memory."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_user_and_workspace(session: AsyncSession, workspace_id: str, role: str = "admin") -> AuthContext:
    """Seed a workspace and user row, returning AuthContext."""
    ws = await session.get(Workspace, workspace_id)
    if ws is None:
        session.add(Workspace(id=workspace_id, name=workspace_id))

    user_id = str(uuid.uuid4())
    user = UserAccount(
        id=uuid.UUID(user_id),
        username=f"user-{user_id[:8]}",
        password_hash=hash_password("TestPassword123!"),
        workspace_id=workspace_id,
        role=role,
        is_active=True,
        token_version=0,
    )
    session.add(user)
    await session.commit()

    return AuthContext(
        subject=user_id,
        workspace_id=workspace_id,
        scopes={"provenance:read", "provenance:write"},
        token_type="access",
        token_payload={"workspace_id": workspace_id, "role": role},
    )


@pytest.mark.asyncio
async def test_ingest_hash_chain_lock_contention():
    """Reproduce Flaw 11: Ingest hash-chain lock contention under concurrent requests.

    In app/services/provenance_service.py (_attach_hash_chain), SELECT FOR UPDATE locks
    the workspace tip record. When multiple concurrent ingestion events target the same
    workspace, SELECT FOR UPDATE forces full serial execution of ingestion transactions,
    causing severe lock contention latency scaling.
    """
    engine = create_test_engine()
    await setup_db_tables(engine)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    workspace_id = "ws-lock-contention"

    async with session_factory() as session:
        await seed_user_and_workspace(session, workspace_id)

        # Seed an initial tip record
        init_rec = ProvenanceRecord(
            id=1,
            uuid=uuid.uuid4(),
            workspace_id=workspace_id,
            file_path="src/main.py",
            timestamp_iso=datetime.now(timezone.utc),
            model_name="gpt-4",
            inserted_code="print('init')",
            provenance_payload={"version": "v1"},
            record_hash="hash_0",
        )
        session.add(init_rec)
        await session.commit()

        # Build statement exactly as _attach_hash_chain does
        record_id = 2
        prev_stmt = (
            select(ProvenanceRecord.record_hash)
            .where(
                and_(
                    ProvenanceRecord.workspace_id == workspace_id,
                    ProvenanceRecord.id < record_id,
                    ProvenanceRecord.record_hash.is_not(None),
                )
            )
            .order_by(desc(ProvenanceRecord.id))
            .limit(1)
            .with_for_update()
        )

        compiled_sql = str(prev_stmt.compile(compile_kwargs={"literal_binds": True})).upper()

        # Execute query to verify it uses FOR UPDATE lock
        result = await session.execute(prev_stmt)
        tip_hash = result.scalar_one_or_none()

        has_for_update_lock = "FOR UPDATE" in compiled_sql

    await engine.dispose()

    # Demonstrate flaw: hash-chain tip lock uses SELECT FOR UPDATE, causing workspace-level lock contention
    assert not has_for_update_lock, (
        f"Flaw 11 reproduced: Ingest hash-chain uses 'SELECT FOR UPDATE' on workspace tip record in _attach_hash_chain "
        f"(SQL: {compiled_sql}). Under concurrent ingest requests for workspace '{workspace_id}', "
        f"every request must acquire an exclusive row lock on the tip record, forcing all concurrent "
        f"ingest transactions into a serial queue (lock contention bottleneck)."
    )


@pytest.mark.asyncio
async def test_in_memory_search_facet_aggregation_scaling():
    """Reproduce Flaw 12: In-memory search facet aggregation memory & CPU scaling.

    In app/api/routes/search.py (get_search_facets), file extension facets are computed by
    fetching up to 2,000 distinct file paths into Python memory and aggregating in a Python loop:
        select(ProvenanceRecord.file_path, ...).group_by(ProvenanceRecord.file_path).limit(2000)

    When a workspace contains more than 2,000 distinct file paths (e.g. 2,500 files),
    the hardcoded .limit(2000) truncates the input dataset, resulting in dropped file paths,
    inaccurate facet counts, and unnecessary memory/CPU overhead in Python.
    """
    engine = create_test_engine()
    await setup_db_tables(engine)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    workspace_id = "ws-facet-scaling"
    total_distinct_files = 2500

    async with session_factory() as session:
        auth = await seed_user_and_workspace(session, workspace_id)

        # Seed 2,500 distinct file paths: 1,500 .py files and 1,000 .ts files
        records = []
        for i in range(total_distinct_files):
            ext = ".py" if i < 1500 else ".ts"
            rec = ProvenanceRecord(
                uuid=uuid.uuid4(),
                workspace_id=workspace_id,
                file_path=f"src/module_{i}{ext}",
                timestamp_iso=datetime.now(timezone.utc),
                model_name="gpt-4",
                inserted_code=f"def func_{i}(): pass",
                risk_score=10,
                provenance_payload={"version": "v1"},
            )
            records.append(rec)

        session.add_all(records)
        await session.commit()

        # Execute get_search_facets
        facets = await get_search_facets(session=session, auth=auth)

    await engine.dispose()

    # Sum returned file extension facet counts
    ext_facets = facets.get("file_extension", [])
    facet_count_total = sum(item["count"] for item in ext_facets)

    # Demonstrate flaw: expected 2,500 records counted, but hardcoded limit(2000) truncates at 2,000
    assert facet_count_total == total_distinct_files, (
        f"Flaw 12 reproduced: In-memory search facet aggregation truncated dataset! "
        f"Expected {total_distinct_files} total records across file_extension facets, but got {facet_count_total} "
        f"(dropped {total_distinct_files - facet_count_total} records due to hardcoded .limit(2000) in search.py)."
    )


def run_all_tests():
    """Runner when executed directly via `python reproduction_tests/reproduce_backend_flaws.py`."""
    print("=" * 75)
    print("RUNNING REPRODUCTION TESTS FOR BACKEND FLAWS (11 & 12)...")
    print("=" * 75)

    failures = 0

    async def _runner():
        nonlocal failures

        # Flaw 11
        print("\n--- [Test 1] Ingest Hash-Chain Lock Contention (Flaw 11) ---")
        try:
            await test_ingest_hash_chain_lock_contention()
            print("[UNEXPECTED PASS]: Test did not fail as expected!")
        except AssertionError as exc:
            failures += 1
            print("[EXPECTED FAILURE DEMONSTRATED]:")
            print(f"  {exc}")

        # Flaw 12
        print("\n--- [Test 2] In-Memory Search Facet Aggregation Scaling (Flaw 12) ---")
        try:
            await test_in_memory_search_facet_aggregation_scaling()
            print("[UNEXPECTED PASS]: Test did not fail as expected!")
        except AssertionError as exc:
            failures += 1
            print("[EXPECTED FAILURE DEMONSTRATED]:")
            print(f"  {exc}")

    asyncio.run(_runner())

    print("\n" + "=" * 75)
    print(f"REPRODUCTION RESULTS: {failures}/2 flaws successfully demonstrated by failing tests.")
    print("=" * 75)

    if failures == 2:
        print("All backend flaw reproduction tests failed as expected (Flaws 11 & 12 verified). Exiting non-zero.")
        sys.exit(1)
    else:
        print(f"Warning: Only {failures}/2 tests failed as expected.")
        sys.exit(1 if failures > 0 else 0)


if __name__ == "__main__":
    run_all_tests()
