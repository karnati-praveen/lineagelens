"""Tests for the F6 human review attestation feature.

Coverage:
- Depth signal computed correctly for normal and edge inputs.
- A 3-second approval of 400 AI lines → depth=shallow, gate fails.
- Signed attestation is verifiable (reuses app.core.attestation, not a duplicate).
- F1 eligibility now sees a real review status via get_review_status.
- Workspace isolation: a review in ws-A is not visible in ws-B.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789")


# ─── Pure-unit: depth signal formula ─────────────────────────────────────────


from app.services.human_review_service import compute_depth_signal


@pytest.mark.parametrize(
    "lines, seconds, comments, expected_signal",
    [
        # Implausibly fast: 3 s for 400 lines → <1 s/line → shallow
        (400, 3, 0, "shallow"),
        # Borderline fast: 400 lines, 399 seconds, no comments
        # time_per_line ≈ 1.0 → time_score=8, coverage=30, comment=0 → raw=38 → adequate
        (400, 400, 0, "adequate"),
        # Deep review: 50 lines, 300 s, 5 comments
        # time_per_line=6 → time_score=40, comment_score=30, coverage=30 → raw=100 → deep
        (50, 300, 5, "deep"),
        # Minimal coverage: 1 line, 10 s, 0 comments
        # time_per_line=10 → time_score=40, coverage_score=0.6, comment=0 → ~40.6 → adequate
        (1, 10, 0, "adequate"),
        # No lines, no time: edge-case 0/0 → shallow (time_per_line = 0 < 1)
        (0, 0, 0, "shallow"),
        # Long review with comments → deep
        (200, 1200, 4, "deep"),
    ],
)
def test_depth_signal_formula(lines, seconds, comments, expected_signal):
    signal, raw = compute_depth_signal(lines, seconds, comments)
    assert signal == expected_signal, (
        f"lines={lines}, secs={seconds}, comments={comments} → signal={signal!r} "
        f"(raw={raw:.1f}), expected {expected_signal!r}"
    )


def test_depth_raw_score_bounds():
    _, raw = compute_depth_signal(200, 1000, 10)
    assert 0.0 <= raw <= 100.0


def test_implausibly_fast_is_always_shallow():
    # 3 s for 400 lines is 0.0075 s/line — the quintessential rubber-stamp.
    signal, raw = compute_depth_signal(400, 3, 10)
    assert signal == "shallow"
    assert raw == 0.0


# ─── Attestation: signed + verifiable via app.core.attestation ────────────────


def test_attestation_is_signed_and_verifiable(client, make_user):
    """POST /review/attest must store a signed attestation that passes verify."""
    import json

    from app.core.attestation import SignedAttestation, verify_attestation
    from app.db.models import Attestation

    user = make_user(role="member")
    resp = client.post(
        "/review/attest",
        json={
            "scopeRef": "test-record-uuid-aaa",
            "linesReviewed": 60,
            "secondsOnDiff": 400,
            "commentCount": 4,
            "verdict": "approved",
        },
        headers=user.auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["depthSignal"] in {"shallow", "adequate", "deep"}
    assert body["attestationId"] is not None

    # Retrieve the raw Attestation row and verify the Ed25519 signature.
    import asyncio

    async def _fetch(session):
        from sqlalchemy import select

        result = await session.execute(
            select(Attestation).where(
                Attestation.workspace_id == user.workspace_id,
                Attestation.subject_type == "review",
            )
        )
        return result.scalar_one_or_none()

    engine_for = _engine_for_from_client(client)
    att_row = asyncio.run(_async_run(engine_for, _fetch))
    assert att_row is not None

    signed = SignedAttestation(
        statement=json.loads(att_row.statement_json),
        signature=att_row.signature,
        public_key_id=att_row.public_key_id,
    )
    assert verify_attestation(signed) is True


# ─── Gate: rubber-stamp approval fails ────────────────────────────────────────


def test_rubber_stamp_gate_fails(client, make_user):
    """3-second approval of 400 AI lines → depth=shallow → gate fails."""
    user = make_user(role="member")

    # POST the shallow attestation.
    resp = client.post(
        "/review/attest",
        json={
            "scopeRef": "pr/rubber-stamp-test",
            "linesReviewed": 400,
            "secondsOnDiff": 3,
            "commentCount": 0,
            "verdict": "approved",
        },
        headers=user.auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["depthSignal"] == "shallow"

    # Seed an API key for the gate call.
    api_key, key_hash = _create_api_key(client, user)

    # Gate should reject because depth=shallow < adequate.
    gate_resp = client.post(
        "/review/gate/pr/rubber-stamp-test",
        headers={"X-API-Key": api_key},
    )
    assert gate_resp.status_code == 200
    gate_body = gate_resp.json()
    assert gate_body["passed"] is False
    assert gate_body["depthSignal"] == "shallow"


# ─── GET /review/status — F1 integration ─────────────────────────────────────


def test_get_review_status_no_review(client, make_user):
    """has_review=False when nothing has been attested for a scope_ref."""
    user = make_user(role="member")
    resp = client.get("/review/status/nonexistent-uuid", headers=user.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["has_review"] is False


def test_get_review_status_after_attest(client, make_user):
    """After POST /review/attest, GET /review/status reflects the result."""
    user = make_user(role="member")
    scope = "record-for-f1-test-bbb"

    client.post(
        "/review/attest",
        json={
            "scopeRef": scope,
            "linesReviewed": 80,
            "secondsOnDiff": 600,
            "commentCount": 3,
            "verdict": "approved",
        },
        headers=user.auth_headers,
    )

    status_resp = client.get(f"/review/status/{scope}", headers=user.auth_headers)
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["has_review"] is True
    assert body["verdict"] == "approved"
    assert body["depth_signal"] in {"adequate", "deep"}
    assert body["attestation_id"] is not None


# ─── Workspace isolation ──────────────────────────────────────────────────────


def test_workspace_isolation(client, make_user):
    """A review attested in workspace A is invisible to workspace B."""
    user_a = make_user(role="member", workspace_id="ws-isolation-a")
    user_b = make_user(role="member", workspace_id="ws-isolation-b")
    scope = "shared-scope-ref-isolation"

    client.post(
        "/review/attest",
        json={
            "scopeRef": scope,
            "linesReviewed": 50,
            "secondsOnDiff": 300,
            "commentCount": 2,
            "verdict": "approved",
        },
        headers=user_a.auth_headers,
    )

    # Workspace B sees no review.
    resp_b = client.get(f"/review/status/{scope}", headers=user_b.auth_headers)
    assert resp_b.status_code == 200
    assert resp_b.json()["has_review"] is False

    # Workspace A sees the review.
    resp_a = client.get(f"/review/status/{scope}", headers=user_a.auth_headers)
    assert resp_a.status_code == 200
    assert resp_a.json()["has_review"] is True


# ─── Invalid inputs ───────────────────────────────────────────────────────────


def test_invalid_verdict_rejected(client, make_user):
    user = make_user(role="member")
    resp = client.post(
        "/review/attest",
        json={"scopeRef": "x", "linesReviewed": 10, "secondsOnDiff": 60, "verdict": "lgtm"},
        headers=user.auth_headers,
    )
    assert resp.status_code == 400


def test_gate_no_attestation_fails(client, make_user):
    user = make_user(role="member")
    api_key, _ = _create_api_key(client, user)
    resp = client.post("/review/gate/pr/no-attest-yet", headers={"X-API-Key": api_key})
    assert resp.status_code == 200
    assert resp.json()["passed"] is False


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _engine_for_from_client(client):
    """Return the SQLite database URL stored on the test client."""
    return getattr(client, "database_url", None)


async def _async_run(database_url, async_fn):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            return await async_fn(session)
    finally:
        await engine.dispose()


def _create_api_key(client, user) -> tuple[str, str]:
    """POST to /api-keys and return (raw_key, key_hash)."""
    import hashlib
    import secrets

    raw = "lltest-" + secrets.token_hex(16)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()

    resp = client.post(
        "/api-keys",
        json={"name": "test-gate-key", "scopes": []},
        headers=user.auth_headers,
    )
    # If the endpoint returns the key, use it; otherwise plant one directly via db_query.
    if resp.status_code in (200, 201):
        body = resp.json()
        raw_from_api = body.get("key") or body.get("apiKey") or raw
        return raw_from_api, hashlib.sha256(raw_from_api.encode()).hexdigest()

    # Fallback: seed directly.
    import asyncio
    import uuid as uuid_pkg

    async def _seed(session):
        from app.db.models import ApiKey

        ak = ApiKey(
            workspace_id=user.workspace_id,
            user_id=user.id,
            name="test-gate-key",
            key_prefix=raw[:8],
            key_hash=key_hash,
            scopes=[],
            is_active=True,
        )
        session.add(ak)
        await session.commit()

    asyncio.run(_async_run(client.database_url, _seed))
    return raw, key_hash
