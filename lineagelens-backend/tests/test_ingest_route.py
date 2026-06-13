"""API contract tests for POST /ingest.

Focus: status codes, auth enforcement, response shape, idempotency — not the
internals of payload normalization (those are covered by test_ingest_normalizer).

Run with:
    cd lineagelens-backend && pytest tests/test_ingest_route.py -q
"""
from __future__ import annotations

import uuid as uuid_pkg

from sqlalchemy import func, select

from app.db.models import ProvenanceRecord


def _valid_payload(workspace_id: str, **overrides) -> dict:
    payload = {
        "workspaceId": workspace_id,
        "filePath": "/srv/app/main.py",
        "insertedText": "print('hello world')\n",
    }
    payload.update(overrides)
    return payload


def test_ingest_happy_path_with_valid_token(client, make_user):
    user = make_user(role="member")

    resp = client.post(
        "/ingest",
        json=_valid_payload(user.workspace_id),
        headers=user.auth_headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stored"] is True
    # uuid present and parseable; workspace echoed back.
    assert uuid_pkg.UUID(body["uuid"])
    assert body["workspaceId"] == user.workspace_id


def test_ingest_requires_token(client, make_user):
    # Seed a user so SetupGuard does not short-circuit with a /setup redirect,
    # then post WITHOUT an Authorization header.
    user = make_user(role="member")

    resp = client.post("/ingest", json=_valid_payload(user.workspace_id))

    assert resp.status_code == 401


def test_ingest_rejects_oversized_body_with_413(client, make_user):
    user = make_user(role="member")
    # Body comfortably larger than the 8 KB test cap (see HTTP_MAX_BODY_BYTES).
    payload = _valid_payload(user.workspace_id, insertedText="x" * 9000)

    resp = client.post("/ingest", json=payload, headers=user.auth_headers)

    assert resp.status_code == 413


def test_ingest_idempotency_key_does_not_duplicate(client, make_user, db_query):
    user = make_user(role="member")
    key = str(uuid_pkg.uuid4())
    # The proxy sends X-Idempotency-Key equal to the event id, so a retried POST
    # resolves to the same record_uuid.
    payload = _valid_payload(user.workspace_id, id=key)
    headers = {**user.auth_headers, "X-Idempotency-Key": key}

    first = client.post("/ingest", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["stored"] is True
    assert first.json()["uuid"] == key

    second = client.post("/ingest", json=payload, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["stored"] is False
    assert second.json()["uuid"] == key

    async def _count(session):
        return await session.scalar(
            select(func.count())
            .select_from(ProvenanceRecord)
            .where(ProvenanceRecord.uuid == uuid_pkg.UUID(key))
        )

    assert db_query(_count) == 1


def test_ingest_malformed_payload_missing_file_path_is_4xx(client, make_user):
    user = make_user(role="member")
    # Valid JSON object but no file path anywhere -> normalizer raises ValueError,
    # which the route maps to 400 (never an unhandled 500).
    bad = {"workspaceId": user.workspace_id, "insertedText": "x"}

    resp = client.post("/ingest", json=bad, headers=user.auth_headers)

    assert 400 <= resp.status_code < 500
    assert resp.status_code != 500


def test_ingest_non_object_body_is_4xx_not_500(client, make_user):
    user = make_user(role="member")

    resp = client.post("/ingest", json=[1, 2, 3], headers=user.auth_headers)

    assert 400 <= resp.status_code < 500
    assert resp.status_code != 500


def test_ingest_invalid_json_is_4xx_not_500(client, make_user):
    user = make_user(role="member")

    resp = client.post(
        "/ingest",
        data="{not valid json",
        headers={**user.auth_headers, "Content-Type": "application/json"},
    )

    assert 400 <= resp.status_code < 500
    assert resp.status_code != 500
