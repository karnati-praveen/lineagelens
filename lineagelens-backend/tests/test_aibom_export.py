"""Tests for AI-BOM generation: schema shape, signature, tamper detection, and mode gating."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
)

from app.services.aibom_service import generate_aibom
from app.services.integrity_service import verify_aibom_signature


# ── helpers ───────────────────────────────────────────────────────────────────

_BASE_CFG = {
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789",
    "BACKEND_CORS_ORIGINS": "http://localhost:3000",
}


def _settings(**kw):
    from app.core.config import Settings
    return Settings.model_validate({**_BASE_CFG, **kw})


class _Scalar:
    def __init__(self, records):
        self._records = records

    def all(self):
        return self._records


class _ExecResult:
    def __init__(self, records):
        self._records = records

    def scalars(self):
        return _Scalar(self._records)


class _FakeSession:
    def __init__(self, records=None):
        self._records = records or []
        self.added = []
        self.flush_calls = 0

    async def execute(self, _stmt):
        await asyncio.sleep(0)
        return _ExecResult(self._records)

    def add(self, r):
        self.added.append(r)

    async def flush(self):
        self.flush_calls += 1


def _ts(s: str = "2026-06-01T10:00:00") -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _rec(
    uuid,
    *,
    model="claude-opus-4-5",
    prompt=None,
    risk=0.2,
    rhash=None,
    phash=None,
):
    return SimpleNamespace(
        uuid=uuid,
        file_path=f"src/{uuid}.py",
        model_name=model,
        prompt_messages=prompt,
        risk_score=risk,
        is_redacted=False,
        record_hash=rhash,
        prev_hash=phash,
        provenance_payload={},
        timestamp_iso=_ts(),
    )


# ── schema shape ──────────────────────────────────────────────────────────────

def test_aibom_top_level_keys():
    aibom = asyncio.run(generate_aibom(_FakeSession([_rec("r1")]), "ws"))
    for k in ("schema_version", "generated_at", "workspace_id", "filter", "summary", "records", "signature"):
        assert k in aibom, f"missing top-level key: {k!r}"


def test_aibom_schema_version_is_1_0():
    aibom = asyncio.run(generate_aibom(_FakeSession([_rec("r1")]), "ws"))
    assert aibom["schema_version"] == "1.0"


def test_aibom_signature_algorithm_and_length():
    aibom = asyncio.run(generate_aibom(_FakeSession([_rec("r1")]), "ws"))
    assert aibom["signature"]["algorithm"] == "hmac-sha256"
    assert len(aibom["signature"]["value"]) == 64
    assert all(c in "0123456789abcdef" for c in aibom["signature"]["value"])


def test_aibom_summary_model_counts():
    records = [
        _rec("r1", model="gpt-4o", prompt=[{"role": "user", "content": "hello"}]),
        _rec("r2", model="gpt-4o"),
        _rec("r3", model="claude-opus-4-5"),
    ]
    aibom = asyncio.run(generate_aibom(_FakeSession(records), "ws"))
    s = aibom["summary"]
    assert s["total_records"] == 3
    assert s["by_model"]["gpt-4o"] == 2
    assert s["by_model"]["claude-opus-4-5"] == 1
    # Only r1 has prompt_messages — disclosure_coverage_pct = 1/3
    assert s["disclosure_coverage_pct"] == round(1 / 3 * 100, 1)


def test_aibom_empty_workspace_returns_valid_structure():
    aibom = asyncio.run(generate_aibom(_FakeSession([]), "ws-empty"))
    assert aibom["summary"]["total_records"] == 0
    assert aibom["summary"]["chain_verified"] is True
    assert aibom["records"] == []
    assert "signature" in aibom


def test_aibom_records_have_expected_fields():
    records = [_rec("r1", model="gpt-4o", prompt=[{"role": "user", "content": "hello"}])]
    aibom = asyncio.run(generate_aibom(_FakeSession(records), "ws"))
    entry = aibom["records"][0]
    for field in ("uuid", "file_path", "model_name", "prompt_sha256", "risk_score",
                  "risk_reasons", "timestamp_iso", "is_redacted", "record_hash", "prev_hash"):
        assert field in entry, f"missing record field: {field!r}"


# ── signature ──────────────────────────────────────────────────────────────────

def test_aibom_signature_verifies():
    """The signature in the returned payload must verify against the payload body."""
    aibom = asyncio.run(generate_aibom(_FakeSession([_rec("r1")]), "ws"))
    sig = aibom["signature"]["value"]
    payload_body = {k: v for k, v in aibom.items() if k != "signature"}
    canonical = json.dumps(payload_body, sort_keys=True, default=str)
    assert verify_aibom_signature(canonical, sig) is True


def test_aibom_signature_fails_after_mutation():
    """Mutating any summary field must invalidate the signature."""
    aibom = asyncio.run(generate_aibom(_FakeSession([_rec("r1")]), "ws"))
    sig = aibom["signature"]["value"]
    # Tamper with the summary
    aibom["summary"]["total_records"] = 9999
    payload_body = {k: v for k, v in aibom.items() if k != "signature"}
    canonical_mutated = json.dumps(payload_body, sort_keys=True, default=str)
    assert verify_aibom_signature(canonical_mutated, sig) is False


def test_aibom_signature_fails_after_record_mutation():
    """Mutating a record entry's risk_score must also invalidate the signature."""
    records = [_rec("r1"), _rec("r2")]
    aibom = asyncio.run(generate_aibom(_FakeSession(records), "ws"))
    sig = aibom["signature"]["value"]
    aibom["records"][0]["risk_score"] = 0.99
    payload_body = {k: v for k, v in aibom.items() if k != "signature"}
    canonical_mutated = json.dumps(payload_body, sort_keys=True, default=str)
    assert verify_aibom_signature(canonical_mutated, sig) is False


# ── mode guard unit tests ─────────────────────────────────────────────────────

def test_require_non_solo_raises_403_in_solo_mode():
    from fastapi import HTTPException
    from app.core.mode_guard import require_non_solo
    with pytest.raises(HTTPException) as exc_info:
        require_non_solo(_settings(BACKEND_MODE="solo"))
    assert exc_info.value.status_code == 403


def test_require_non_solo_passes_in_team_mode():
    from app.core.mode_guard import require_non_solo
    require_non_solo(_settings(BACKEND_MODE="team"))  # must not raise


def test_require_non_solo_passes_in_enterprise_mode():
    from app.core.mode_guard import require_non_solo
    require_non_solo(_settings(BACKEND_MODE="enterprise"))  # must not raise


# ── HTTP gating tests ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def _base_app():
    """Return the FastAPI app with aiosqlite available; skip otherwise."""
    try:
        import aiosqlite  # noqa: F401
    except ModuleNotFoundError:
        pytest.skip("aiosqlite not installed — skipping HTTP gating tests")
    from app.main import app
    # Signal setup-complete so SetupGuardMiddleware passes through.
    app.state.setup_complete = True
    return app


@pytest.fixture
def _solo_client(_base_app):
    """TestClient with all dependencies overridden for solo-mode gating tests."""
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.security import get_current_auth_context, AuthContext
    from app.db.session import get_db_session

    fake_auth = AuthContext(
        subject="00000000-0000-4000-8000-000000000001",
        workspace_id="ws-test",
        scopes=set(),
        token_type="bearer",
        token_payload={},
    )

    async def _fake_db():
        yield _FakeSession()

    _base_app.dependency_overrides[get_settings] = lambda: _settings(BACKEND_MODE="solo")
    _base_app.dependency_overrides[get_current_auth_context] = lambda: fake_auth
    _base_app.dependency_overrides[get_db_session] = _fake_db

    with TestClient(_base_app, raise_server_exceptions=False) as c:
        yield c

    _base_app.dependency_overrides.pop(get_settings, None)
    _base_app.dependency_overrides.pop(get_current_auth_context, None)
    _base_app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def _team_client(_base_app):
    """TestClient with all dependencies overridden for team-mode gating tests."""
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.security import get_current_auth_context, AuthContext
    from app.db.session import get_db_session

    fake_auth = AuthContext(
        subject="00000000-0000-4000-8000-000000000001",
        workspace_id="ws-test",
        scopes=set(),
        token_type="bearer",
        token_payload={},
    )

    async def _fake_db():
        yield _FakeSession()

    _base_app.dependency_overrides[get_settings] = lambda: _settings(BACKEND_MODE="team")
    _base_app.dependency_overrides[get_current_auth_context] = lambda: fake_auth
    _base_app.dependency_overrides[get_db_session] = _fake_db

    with TestClient(_base_app, raise_server_exceptions=False) as c:
        yield c

    _base_app.dependency_overrides.pop(get_settings, None)
    _base_app.dependency_overrides.pop(get_current_auth_context, None)
    _base_app.dependency_overrides.pop(get_db_session, None)


def test_integrity_verify_returns_403_in_solo_mode(_solo_client):
    resp = _solo_client.get("/integrity/verify", params={"workspace_id": "ws-test"})
    assert resp.status_code == 403


def test_integrity_aibom_returns_403_in_solo_mode(_solo_client):
    resp = _solo_client.post("/integrity/aibom", params={"workspace_id": "ws-test"})
    assert resp.status_code == 403


def test_integrity_verify_passes_mode_guard_in_team_mode(_team_client):
    resp = _team_client.get("/integrity/verify", params={"workspace_id": "ws-test"})
    # Mode guard passed — response must NOT be the 403 Lite-upgrade error
    assert resp.status_code != 403


def test_integrity_aibom_passes_mode_guard_in_team_mode(_team_client):
    resp = _team_client.post("/integrity/aibom", params={"workspace_id": "ws-test"})
    assert resp.status_code != 403
