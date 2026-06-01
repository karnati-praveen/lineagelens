"""Tests for GET /api/v1/aibom/{record_id}/export endpoint."""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
)

_BASE_CFG = {
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
    "BACKEND_CORS_ORIGINS": "http://localhost:3000",
}

_KNOWN_RECORD_ID = "aaaaaaaa-0000-4000-8000-000000000001"
_KNOWN_WORKSPACE_ID = "ws-test"


def _settings(**kw):
    from app.core.config import Settings
    return Settings.model_validate({**_BASE_CFG, **kw})


def _rec():
    """Return a fake ProvenanceRecord-like object."""
    return SimpleNamespace(
        uuid=_KNOWN_RECORD_ID,
        workspace_id=_KNOWN_WORKSPACE_ID,
        file_path="src/foo.py",
        model_name="claude-opus-4-5",
        prompt_messages=None,
        risk_score=0.2,
        is_redacted=False,
        record_hash=None,
        prev_hash=None,
        provenance_payload={},
        timestamp_iso=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )


class _ScalarResult:
    """Supports both scalars().all() and scalar_one_or_none()."""

    def __init__(self, record=None):
        self._record = record

    def scalars(self):
        return _AllResult([self._record] if self._record is not None else [])

    def scalar_one_or_none(self):
        return self._record


class _AllResult:
    def __init__(self, records):
        self._records = records

    def all(self):
        return self._records


class _FakeSession:
    def __init__(self, record=None):
        self._record = record
        self.added = []
        self.flush_calls = 0

    async def execute(self, _stmt):
        await asyncio.sleep(0)
        return _ScalarResult(self._record)

    def add(self, r):
        self.added.append(r)

    async def flush(self):
        self.flush_calls += 1


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _base_app():
    """Return the FastAPI app; skip if aiosqlite is unavailable."""
    try:
        import aiosqlite  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("aiosqlite not installed — skipping HTTP endpoint tests")
    from app.main import app
    app.state.setup_complete = True
    return app


def _make_client(base_app, mode: str, record=None):
    """Build a TestClient with overridden dependencies for the given mode."""
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.security import get_current_auth_context, AuthContext
    from app.db.session import get_db_session

    fake_auth = AuthContext(
        subject="00000000-0000-4000-8000-000000000001",
        workspace_id=_KNOWN_WORKSPACE_ID,
        scopes=set(),
        token_type="bearer",
        token_payload={},
    )

    async def _fake_db():
        yield _FakeSession(record)

    base_app.dependency_overrides[get_settings] = lambda: _settings(BACKEND_MODE=mode)
    base_app.dependency_overrides[get_current_auth_context] = lambda: fake_auth
    base_app.dependency_overrides[get_db_session] = _fake_db

    client = TestClient(base_app, raise_server_exceptions=False)
    return client


@pytest.fixture
def _solo_client(_base_app):
    from app.core.config import get_settings
    from app.core.security import get_current_auth_context
    from app.db.session import get_db_session

    client = _make_client(_base_app, mode="solo", record=None)
    with client as c:
        yield c

    _base_app.dependency_overrides.pop(get_settings, None)
    _base_app.dependency_overrides.pop(get_current_auth_context, None)
    _base_app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def _team_client_no_record(_base_app):
    from app.core.config import get_settings
    from app.core.security import get_current_auth_context
    from app.db.session import get_db_session

    client = _make_client(_base_app, mode="team", record=None)
    with client as c:
        yield c

    _base_app.dependency_overrides.pop(get_settings, None)
    _base_app.dependency_overrides.pop(get_current_auth_context, None)
    _base_app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def _team_client_with_record(_base_app):
    from app.core.config import get_settings
    from app.core.security import get_current_auth_context
    from app.db.session import get_db_session

    client = _make_client(_base_app, mode="team", record=_rec())
    with client as c:
        yield c

    _base_app.dependency_overrides.pop(get_settings, None)
    _base_app.dependency_overrides.pop(get_current_auth_context, None)
    _base_app.dependency_overrides.pop(get_db_session, None)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_export_aibom_returns_403_in_solo_mode(_solo_client):
    """Mode guard must block the endpoint in solo mode."""
    resp = _solo_client.get(f"/api/v1/aibom/{_KNOWN_RECORD_ID}/export")
    assert resp.status_code == 403


def test_export_aibom_passes_mode_guard_in_team_mode(_team_client_no_record):
    """Mode guard must NOT return 403 in team mode (record missing → 404, not 403)."""
    resp = _team_client_no_record.get(f"/api/v1/aibom/{_KNOWN_RECORD_ID}/export")
    assert resp.status_code != 403


def test_export_aibom_returns_404_when_record_not_found(_team_client_no_record):
    """When no provenance record exists for the given UUID, expect 404."""
    resp = _team_client_no_record.get(f"/api/v1/aibom/nonexistent-uuid/export")
    assert resp.status_code == 404


def test_export_aibom_content_disposition_header(_team_client_with_record):
    """A successful response must carry Content-Disposition: attachment."""
    resp = _team_client_with_record.get(f"/api/v1/aibom/{_KNOWN_RECORD_ID}/export")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
