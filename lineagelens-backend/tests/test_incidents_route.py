"""API contract tests for /incidents.

Covers:
- CRUD happy paths + admin-only gates + empty-files rejection
- Workspace isolation (workspace B cannot see workspace A's incidents)
- Webhook: valid signature accepted, invalid signature → 401, unconfigured → 403
- Provenance payoff: basename match, records after started_at excluded,
  workspace isolation, ordering by risk DESC
- Migration sanity: _CURRENT_ALEMBIC_HEAD equals the new revision string

Run with:
    cd lineagelens-backend && pytest tests/test_incidents_route.py -q
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta

from app.db.session import _CURRENT_ALEMBIC_HEAD

_REVISION = "202606150001"


# ─── Migration sanity ─────────────────────────────────────────────────────────

def test_current_alembic_head_matches_revision():
    assert _CURRENT_ALEMBIC_HEAD == _REVISION


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_payload(files=None, started_offset_hours: float = -2.0):
    started = datetime.now(UTC) + timedelta(hours=started_offset_hours)
    return {
        "title": "Test incident",
        "startedAt": started.isoformat(),
        "affectedFiles": files or ["/app/src/main.py"],
    }


def _create(client, user, files=None, started_offset_hours: float = -2.0):
    resp = client.post(
        "/incidents",
        json=_make_payload(files, started_offset_hours),
        headers=user.auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get_started_at(client, user, incident_uuid: str) -> datetime:
    resp = client.get(f"/incidents/{incident_uuid}", headers=user.auth_headers)
    assert resp.status_code == 200
    return datetime.fromisoformat(resp.json()["startedAt"])


def _engine_for(database_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool
    return create_async_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )


def _seed_provenance(db_url: str, workspace_id: str, file_path: str, timestamp: datetime, risk: int | None = None) -> str:
    rec_uuid = uuid_pkg.uuid4()

    async def _run():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from app.db.models import ProvenanceRecord
        engine = _engine_for(db_url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                rec = ProvenanceRecord(
                    uuid=rec_uuid,
                    workspace_id=workspace_id,
                    file_path=file_path,
                    timestamp_iso=timestamp,
                    inserted_code="# ai generated",
                    provenance_payload={},
                    risk_score=risk,
                )
                session.add(rec)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return str(rec_uuid)


def _seed_webhook_config(db_url: str, workspace_id: str, raw_secret: str) -> None:
    from app.core.encryption import encrypt_field

    async def _run():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from app.db.models import IncidentIntegration
        engine = _engine_for(db_url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                cfg = IncidentIntegration(
                    id=uuid_pkg.uuid4(),
                    workspace_id=workspace_id,
                    webhook_secret=encrypt_field(raw_secret),
                )
                session.add(cfg)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ─── CRUD happy paths ─────────────────────────────────────────────────────────

def test_create_incident_happy(client, make_user):
    admin = make_user(role="admin")
    resp = client.post("/incidents", json=_make_payload(), headers=admin.auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Test incident"
    assert "/app/src/main.py" in body["affectedFiles"]
    assert body["resolvedAt"] is None


def test_create_incident_requires_admin(client, make_user):
    member = make_user(role="member")
    resp = client.post("/incidents", json=_make_payload(), headers=member.auth_headers)
    assert resp.status_code == 403


def test_create_incident_rejects_empty_files(client, make_user):
    admin = make_user(role="admin")
    payload = _make_payload()
    payload["affectedFiles"] = []
    resp = client.post("/incidents", json=payload, headers=admin.auth_headers)
    assert resp.status_code == 422


def test_list_incidents(client, make_user):
    admin = make_user(role="admin")
    _create(client, admin)
    _create(client, admin, files=["/other/file.py"])

    resp = client.get("/incidents", headers=admin.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2


def test_list_incidents_pagination(client, make_user):
    admin = make_user(role="admin")
    for _ in range(3):
        _create(client, admin)

    resp = client.get("/incidents?limit=2&offset=0", headers=admin.auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2

    resp2 = client.get("/incidents?limit=2&offset=2", headers=admin.auth_headers)
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 1


def test_get_incident(client, make_user):
    admin = make_user(role="admin")
    inc = _create(client, admin)
    resp = client.get(f"/incidents/{inc['uuid']}", headers=admin.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["uuid"] == inc["uuid"]


def test_get_incident_not_found(client, make_user):
    member = make_user(role="member")
    resp = client.get(f"/incidents/{uuid_pkg.uuid4()}", headers=member.auth_headers)
    assert resp.status_code == 404


def test_patch_incident_resolves(client, make_user):
    admin = make_user(role="admin")
    inc = _create(client, admin)
    resolved = datetime.now(UTC).isoformat()
    resp = client.patch(
        f"/incidents/{inc['uuid']}",
        json={"resolvedAt": resolved},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["resolvedAt"] is not None


def test_patch_incident_updates_title_and_files(client, make_user):
    admin = make_user(role="admin")
    inc = _create(client, admin)
    resp = client.patch(
        f"/incidents/{inc['uuid']}",
        json={"title": "Updated title", "affectedFiles": ["/new/path.py"]},
        headers=admin.auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Updated title"
    assert body["affectedFiles"] == ["/new/path.py"]


def test_patch_requires_admin(client, make_user):
    ws = f"ws-{uuid_pkg.uuid4().hex[:6]}"
    admin = make_user(role="admin", workspace_id=ws, username=f"adm-{uuid_pkg.uuid4().hex[:6]}")
    member = make_user(role="member", workspace_id=ws, username=f"mem-{uuid_pkg.uuid4().hex[:6]}")
    inc = _create(client, admin)
    resp = client.patch(
        f"/incidents/{inc['uuid']}",
        json={"title": "No access"},
        headers=member.auth_headers,
    )
    assert resp.status_code == 403


# ─── Workspace isolation ──────────────────────────────────────────────────────

def test_workspace_isolation_get(client, make_user):
    admin_a = make_user(role="admin", workspace_id=f"ws-a-{uuid_pkg.uuid4().hex[:6]}", username=f"aa-{uuid_pkg.uuid4().hex[:6]}")
    admin_b = make_user(role="admin", workspace_id=f"ws-b-{uuid_pkg.uuid4().hex[:6]}", username=f"ab-{uuid_pkg.uuid4().hex[:6]}")

    inc = _create(client, admin_a)

    resp = client.get(f"/incidents/{inc['uuid']}", headers=admin_b.auth_headers)
    assert resp.status_code == 404


def test_workspace_isolation_list(client, make_user):
    admin_a = make_user(role="admin", workspace_id=f"ws-a-{uuid_pkg.uuid4().hex[:6]}", username=f"aa-{uuid_pkg.uuid4().hex[:6]}")
    admin_b = make_user(role="admin", workspace_id=f"ws-b-{uuid_pkg.uuid4().hex[:6]}", username=f"ab-{uuid_pkg.uuid4().hex[:6]}")

    _create(client, admin_a)

    resp = client.get("/incidents", headers=admin_b.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ─── Webhook ──────────────────────────────────────────────────────────────────

_WH_SECRET = "test-webhook-secret-abc123!"


def test_webhook_valid_signature_accepted(client, make_user):
    admin = make_user(role="admin")
    _seed_webhook_config(client.database_url, admin.workspace_id, _WH_SECRET)

    body = json.dumps({
        "title": "Prod error",
        "startedAt": datetime.now(UTC).isoformat(),
        "files": ["/app/handler.py"],
        "source": "pagerduty",
    }).encode()

    resp = client.post(
        "/incidents/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-LineageLens-Workspace": admin.workspace_id,
            "X-Hub-Signature-256": _sign(_WH_SECRET, body),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["received"] is True
    assert "incidentUuid" in data


def test_webhook_invalid_signature_rejected(client, make_user):
    admin = make_user(role="admin")
    _seed_webhook_config(client.database_url, admin.workspace_id, _WH_SECRET)

    body = json.dumps({"title": "Prod error", "startedAt": datetime.now(UTC).isoformat(), "files": []}).encode()
    resp = client.post(
        "/incidents/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-LineageLens-Workspace": admin.workspace_id,
            "X-Hub-Signature-256": "sha256=badhash",
        },
    )
    assert resp.status_code == 401


def test_webhook_unconfigured_workspace_returns_403(client, make_user):
    admin = make_user(role="admin")
    # Deliberately do NOT seed a webhook config

    body = json.dumps({"title": "Prod error", "startedAt": datetime.now(UTC).isoformat()}).encode()
    resp = client.post(
        "/incidents/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-LineageLens-Workspace": admin.workspace_id,
            "X-Hub-Signature-256": _sign(_WH_SECRET, body),
        },
    )
    assert resp.status_code == 403


def test_webhook_sentry_shape_accepted(client, make_user):
    admin = make_user(role="admin")
    _seed_webhook_config(client.database_url, admin.workspace_id, _WH_SECRET)

    sentry_payload = {
        "id": "abc123",
        "event": {
            "title": "TypeError: cannot unpack",
            "timestamp": datetime.now(UTC).timestamp(),
            "entries": [
                {
                    "type": "exception",
                    "data": {
                        "values": [
                            {
                                "stacktrace": {
                                    "frames": [
                                        {"filename": "app/services/payment.py", "lineno": 42},
                                        {"filename": "app/models/order.py", "lineno": 17},
                                    ]
                                }
                            }
                        ]
                    },
                }
            ],
        },
    }
    body = json.dumps(sentry_payload).encode()
    resp = client.post(
        "/incidents/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-LineageLens-Workspace": admin.workspace_id,
            "X-Hub-Signature-256": _sign(_WH_SECRET, body),
        },
    )
    assert resp.status_code == 200
    inc_uuid = resp.json()["incidentUuid"]

    # Verify the created incident has files extracted from stacktrace
    get_resp = client.get(f"/incidents/{inc_uuid}", headers=admin.auth_headers)
    assert get_resp.status_code == 200
    files = get_resp.json()["affectedFiles"]
    assert "app/services/payment.py" in files
    assert "app/models/order.py" in files


# ─── Provenance payoff ────────────────────────────────────────────────────────

def test_provenance_returns_records_matching_basename(client, make_user):
    admin = make_user(role="admin")
    # Incident stores absolute path from one machine
    inc = _create(client, admin, files=["/Users/dev/project/app/handler.py"])
    started_at = _get_started_at(client, admin, inc["uuid"])

    # ProvenanceRecord stored with a different absolute prefix but same basename
    _seed_provenance(
        client.database_url,
        admin.workspace_id,
        "/home/ci/project/app/handler.py",
        started_at - timedelta(hours=1),
        risk=75,
    )

    resp = client.get(f"/incidents/{inc['uuid']}/provenance", headers=admin.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


def test_provenance_excludes_records_after_started_at(client, make_user):
    admin = make_user(role="admin")
    inc = _create(client, admin, files=["/app/src/main.py"], started_offset_hours=-2.0)
    started_at = _get_started_at(client, admin, inc["uuid"])

    # Record timestamped 1 hour AFTER the incident started — must be excluded
    _seed_provenance(
        client.database_url,
        admin.workspace_id,
        "/app/src/main.py",
        started_at + timedelta(hours=1),
    )

    resp = client.get(f"/incidents/{inc['uuid']}/provenance", headers=admin.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_provenance_workspace_isolation(client, make_user):
    ws_a = f"ws-a-{uuid_pkg.uuid4().hex[:6]}"
    ws_b = f"ws-b-{uuid_pkg.uuid4().hex[:6]}"
    admin_a = make_user(role="admin", workspace_id=ws_a, username=f"aa-{uuid_pkg.uuid4().hex[:6]}")
    admin_b = make_user(role="admin", workspace_id=ws_b, username=f"ab-{uuid_pkg.uuid4().hex[:6]}")

    inc = _create(client, admin_a, files=["/app/src/main.py"])
    started_at = _get_started_at(client, admin_a, inc["uuid"])

    # Provenance in workspace B — must NOT appear in workspace A's query
    _seed_provenance(
        client.database_url,
        ws_b,
        "/app/src/main.py",
        started_at - timedelta(hours=1),
        risk=80,
    )

    resp = client.get(f"/incidents/{inc['uuid']}/provenance", headers=admin_a.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_provenance_orders_by_risk_desc(client, make_user):
    admin = make_user(role="admin")
    inc = _create(client, admin, files=["/app/main.py"])
    started_at = _get_started_at(client, admin, inc["uuid"])

    _seed_provenance(client.database_url, admin.workspace_id, "/app/main.py", started_at - timedelta(hours=3), risk=20)
    _seed_provenance(client.database_url, admin.workspace_id, "/app/main.py", started_at - timedelta(hours=2), risk=90)
    _seed_provenance(client.database_url, admin.workspace_id, "/app/main.py", started_at - timedelta(hours=1), risk=None)

    resp = client.get(f"/incidents/{inc['uuid']}/provenance", headers=admin.auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3
    # High risk first, null last
    assert items[0]["riskScore"] == 90
    assert items[1]["riskScore"] == 20
    assert items[2]["riskScore"] is None


def test_provenance_respects_window_days(client, make_user):
    admin = make_user(role="admin")
    inc = _create(client, admin, files=["/app/main.py"])
    started_at = _get_started_at(client, admin, inc["uuid"])

    # Record within 7-day window
    _seed_provenance(client.database_url, admin.workspace_id, "/app/main.py", started_at - timedelta(days=5))
    # Record older than 7 days — excluded when windowDays=7
    _seed_provenance(client.database_url, admin.workspace_id, "/app/main.py", started_at - timedelta(days=10))

    resp = client.get(f"/incidents/{inc['uuid']}/provenance?windowDays=7", headers=admin.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
