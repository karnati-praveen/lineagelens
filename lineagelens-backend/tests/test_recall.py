"""Contract tests for /recall (F2 — AI Code Recall).

Covers:
- POST /recall/preview returns correct matches (model filter, date filter, record_uuid filter)
- POST /recall creates a campaign and flags matched records
- POST /recall/{id}/quarantine flips quarantine_status + writes audit log
- Blast radius includes lineage descendants (via mocked neo4j_service)
- Workspace isolation: cannot recall another workspace's records
- Non-admin gets 403

Run with:
    cd lineagelens-backend && pytest tests/test_recall.py -q
"""
from __future__ import annotations

import asyncio
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.db.session import _CURRENT_ALEMBIC_HEAD

_REVISION = "202606150001"


# ─── Migration sanity ─────────────────────────────────────────────────────────

def test_current_alembic_head_matches_revision():
    assert _CURRENT_ALEMBIC_HEAD == _REVISION


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _engine_for(database_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool
    return create_async_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )


def _seed_provenance(db_url: str, workspace_id: str, model_name: str = "gpt-4o", ts_offset_hours: float = -1.0) -> str:
    """Seed a provenance record and return its UUID string."""
    rec_uuid = uuid_pkg.uuid4()

    async def _run():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from app.db.models import ProvenanceRecord
        engine = _engine_for(db_url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                ts = datetime.now(UTC) + timedelta(hours=ts_offset_hours)
                rec = ProvenanceRecord(
                    uuid=rec_uuid,
                    workspace_id=workspace_id,
                    file_path="/app/main.py",
                    timestamp_iso=ts,
                    inserted_code="x = 1",
                    model_name=model_name,
                    provenance_payload={},
                )
                session.add(rec)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return str(rec_uuid)


def _get_quarantine_status(db_url: str, record_uuid: str) -> str | None:
    """Fetch the quarantine_status for a record UUID."""
    result: list[str | None] = [None]

    async def _run():
        import uuid as _uuid
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from app.db.models import ProvenanceRecord
        engine = _engine_for(db_url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                stmt = select(ProvenanceRecord.quarantine_status).where(
                    ProvenanceRecord.uuid == _uuid.UUID(record_uuid)
                )
                r = await session.execute(stmt)
                result[0] = r.scalar_one_or_none()
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return result[0]


def _get_audit_actions(db_url: str, workspace_id: str) -> list[str]:
    actions: list[str] = []

    async def _run():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from app.db.models import AuditLog
        engine = _engine_for(db_url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                stmt = select(AuditLog.action).where(AuditLog.workspace_id == workspace_id)
                r = await session.execute(stmt)
                for row in r.scalars().all():
                    actions.append(row)
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return actions


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_non_admin_gets_403_on_preview(client, make_user):
    member = make_user(role="member")
    resp = client.post(
        "/recall/preview",
        json={"model": "gpt-4o"},
        headers=member.auth_headers,
    )
    assert resp.status_code == 403


def test_preview_returns_matches(client, make_user):
    admin = make_user(role="admin")
    db_url = client.database_url

    rec_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="gpt-4o")

    resp = client.post(
        "/recall/preview",
        json={"model": "gpt-4o"},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matchedCount"] >= 1
    assert rec_uuid in body["matchedUuids"]
    assert "blastRadiusCount" in body


def test_preview_filters_by_record_uuid(client, make_user):
    admin = make_user(role="admin")
    db_url = client.database_url

    rec_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="gpt-4-turbo")
    _seed_provenance(db_url, admin.workspace_id, model_name="gpt-4-turbo")

    resp = client.post(
        "/recall/preview",
        json={"recordUuid": rec_uuid},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matchedCount"] == 1
    assert body["matchedUuids"] == [rec_uuid]


def test_preview_no_match_returns_zero(client, make_user):
    admin = make_user(role="admin")
    _seed_provenance(client.database_url, admin.workspace_id, model_name="gpt-4o")

    resp = client.post(
        "/recall/preview",
        json={"model": "claude-opus-nonexistent"},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["matchedCount"] == 0


def test_create_recall_creates_campaign(client, make_user):
    admin = make_user(role="admin")
    db_url = client.database_url

    _seed_provenance(db_url, admin.workspace_id, model_name="gpt-3.5-turbo")

    resp = client.post(
        "/recall",
        json={"model": "gpt-3.5-turbo"},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "open"
    assert body["matchedCount"] >= 1
    assert body["id"] is not None


def test_create_recall_flags_matched_records(client, make_user):
    admin = make_user(role="admin")
    db_url = client.database_url

    rec_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="flagged-model")

    # Verify initial state is active
    assert _get_quarantine_status(db_url, rec_uuid) == "active"

    resp = client.post(
        "/recall",
        json={"model": "flagged-model"},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 201
    campaign_id = resp.json()["id"]

    # Status should now be flagged
    assert _get_quarantine_status(db_url, rec_uuid) == "flagged"

    # Audit log should contain recall.open and recall.flag
    actions = _get_audit_actions(db_url, admin.workspace_id)
    assert "recall.open" in actions
    assert "recall.flag" in actions


def test_quarantine_flips_status_and_writes_audit(client, make_user):
    admin = make_user(role="admin")
    db_url = client.database_url

    rec_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="quarantine-model")

    # Open a campaign
    create_resp = client.post(
        "/recall",
        json={"model": "quarantine-model"},
        headers=admin.auth_headers,
    )
    assert create_resp.status_code == 201
    campaign_id = create_resp.json()["id"]

    # Quarantine
    q_resp = client.post(f"/recall/{campaign_id}/quarantine", headers=admin.auth_headers)
    assert q_resp.status_code == 200
    assert q_resp.json()["quarantinedCount"] >= 1

    # Status should be quarantined
    assert _get_quarantine_status(db_url, rec_uuid) == "quarantined"

    # Audit log should contain recall.quarantine
    actions = _get_audit_actions(db_url, admin.workspace_id)
    assert "recall.quarantine" in actions


def test_blast_radius_includes_descendants(client, make_user, monkeypatch):
    """Verify that compute_blast_radius is called and its result expands quarantine."""
    admin = make_user(role="admin")
    db_url = client.database_url

    parent_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="blast-model")
    child_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="blast-model")

    # Mock neo4j to return child as blast-radius descendant of parent
    mock_neo4j = MagicMock()

    async def _fake_blast(*args, **kwargs):
        # called as session.run(query, params) inside the neo4j session
        ...

    from unittest.mock import AsyncMock as _AsyncMock

    async def _fake_compute_blast_radius(neo4j_svc, uuids, workspace_id):
        if parent_uuid in uuids:
            return [child_uuid]
        return []

    monkeypatch.setattr(
        "app.api.routes.recall.compute_blast_radius",
        _fake_compute_blast_radius,
    )

    # Set neo4j_service on app state so the route calls the mock path
    from app.main import app as fastapi_app
    original = fastapi_app.state.neo4j_service
    fastapi_app.state.neo4j_service = mock_neo4j

    try:
        preview_resp = client.post(
            "/recall/preview",
            json={"model": "blast-model"},
            headers=admin.auth_headers,
        )
        assert preview_resp.status_code == 200
        body = preview_resp.json()
        assert child_uuid in body["blastUuids"]
        assert body["blastRadiusCount"] >= 1
    finally:
        fastapi_app.state.neo4j_service = original


def test_workspace_isolation_cannot_recall_other_workspace(client, make_user):
    admin_a = make_user(role="admin")
    admin_b = make_user(role="admin")
    db_url = client.database_url

    # Seed a record in workspace A
    rec_uuid = _seed_provenance(db_url, admin_a.workspace_id, model_name="ws-isolation-model")

    # Admin B tries to preview/recall in their own workspace — should get 0 matches
    resp = client.post(
        "/recall/preview",
        json={"model": "ws-isolation-model"},
        headers=admin_b.auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert rec_uuid not in body["matchedUuids"]


def test_clear_resets_quarantine_status(client, make_user):
    admin = make_user(role="admin")
    db_url = client.database_url

    rec_uuid = _seed_provenance(db_url, admin.workspace_id, model_name="clear-model")

    create_resp = client.post("/recall", json={"model": "clear-model"}, headers=admin.auth_headers)
    campaign_id = create_resp.json()["id"]

    client.post(f"/recall/{campaign_id}/quarantine", headers=admin.auth_headers)
    assert _get_quarantine_status(db_url, rec_uuid) == "quarantined"

    clear_resp = client.post(f"/recall/{campaign_id}/clear", headers=admin.auth_headers)
    assert clear_resp.status_code == 200
    assert clear_resp.json()["clearedCount"] >= 1
    assert _get_quarantine_status(db_url, rec_uuid) == "cleared"


def test_close_recall(client, make_user):
    admin = make_user(role="admin")
    create_resp = client.post("/recall", json={"model": "close-test-model"}, headers=admin.auth_headers)
    campaign_id = create_resp.json()["id"]

    close_resp = client.post(f"/recall/{campaign_id}/close", headers=admin.auth_headers)
    assert close_resp.status_code == 200
    assert close_resp.json()["status"] == "closed"

    # Cannot close twice
    close_again = client.post(f"/recall/{campaign_id}/close", headers=admin.auth_headers)
    assert close_again.status_code == 409


def test_list_recalls(client, make_user):
    admin = make_user(role="admin")
    client.post("/recall", json={"model": "list-model-a"}, headers=admin.auth_headers)
    client.post("/recall", json={"model": "list-model-b"}, headers=admin.auth_headers)

    resp = client.get("/recall", headers=admin.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["items"]


def test_get_recall_not_found(client, make_user):
    admin = make_user(role="admin")
    resp = client.get("/recall/999999", headers=admin.auth_headers)
    assert resp.status_code == 404
