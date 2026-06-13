"""API contract tests for the review queue (/reviews).

Covers: approve/reject writes an audit entry, and non-reviewers are forbidden.

Run with:
    cd lineagelens-backend && pytest tests/test_reviews_route.py -q
"""
from __future__ import annotations

import uuid as uuid_pkg

from sqlalchemy import select

from app.db.models import AuditLog


def _audit_actions(db_query, workspace_id: str) -> list[str]:
    async def _fetch(session):
        result = await session.execute(
            select(AuditLog.action).where(AuditLog.workspace_id == workspace_id)
        )
        return [row[0] for row in result.all()]

    return db_query(_fetch)


def _create_review(client, user, record_uuid):
    body = {"workspaceId": user.workspace_id, "recordUuid": record_uuid}
    return client.post("/reviews", json=body, headers=user.auth_headers)


def test_approve_flow_writes_audit_entry(client, make_user, db_query):
    reviewer = make_user(role="reviewer")
    record_uuid = str(uuid_pkg.uuid4())

    created = _create_review(client, reviewer, record_uuid)
    assert created.status_code == 201, created.text
    review_id = created.json()["id"]

    updated = client.patch(
        f"/reviews/{review_id}",
        json={"status": "approved"},
        headers=reviewer.auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "approved"
    assert updated.json()["reviewedBy"] == reviewer.id

    assert "review.update" in _audit_actions(db_query, reviewer.workspace_id)


def test_reject_flow_writes_audit_entry(client, make_user, db_query):
    admin = make_user(role="admin")
    record_uuid = str(uuid_pkg.uuid4())

    review_id = _create_review(client, admin, record_uuid).json()["id"]
    rejected = client.patch(
        f"/reviews/{review_id}",
        json={"status": "rejected"},
        headers=admin.auth_headers,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    assert "review.update" in _audit_actions(db_query, admin.workspace_id)


def test_non_reviewer_cannot_create_review(client, make_user):
    member = make_user(role="member")
    resp = _create_review(client, member, str(uuid_pkg.uuid4()))
    assert resp.status_code == 403


def test_non_reviewer_cannot_update_review(client, make_user):
    # Reviewer creates the item, then a plain member (same workspace) is forbidden
    # from updating it.
    workspace = "rev-ws"
    reviewer = make_user(role="reviewer", workspace_id=workspace, username="rev")
    member = make_user(role="member", workspace_id=workspace, username="mem")

    review_id = _create_review(client, reviewer, str(uuid_pkg.uuid4())).json()["id"]

    resp = client.patch(
        f"/reviews/{review_id}",
        json={"status": "approved"},
        headers=member.auth_headers,
    )
    assert resp.status_code == 403
