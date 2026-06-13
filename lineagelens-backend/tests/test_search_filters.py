"""Tests for the three new POST /search filter features:
  - reviewStatus (unreviewed / pending / reviewed)
  - modelFamily (case-insensitive prefix match)
  - category (ai-category:<slug> tag filter, auto-written at ingest)

All tests run against the SQLite test configuration via the shared conftest fixtures.
"""
from __future__ import annotations

import uuid as uuid_pkg

from sqlalchemy import select

from app.db.models import ProvenanceTag


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ingest(client, user, *, file_path: str = "/srv/app/main.py", inserted_text: str = "x = 1\n", model_name: str | None = None) -> str:
    payload: dict = {
        "workspaceId": user.workspace_id,
        "filePath": file_path,
        "insertedText": inserted_text,
    }
    if model_name:
        payload["provenance"] = {"modelName": model_name}
        # Also set at the top-level field the normalizer reads
        payload["modelName"] = model_name
    resp = client.post("/ingest", json=payload, headers=user.auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["uuid"]


def _search(client, user, **filters) -> list[str]:
    body = {"workspaceId": user.workspace_id, **filters}
    resp = client.post("/search", json=body, headers=user.auth_headers)
    assert resp.status_code == 200, resp.text
    return [r["uuid"] for r in resp.json()["results"]]


def _create_review(client, user, record_uuid: str) -> str:
    resp = client.post(
        "/reviews",
        json={"workspaceId": user.workspace_id, "recordUuid": record_uuid},
        headers=user.auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _set_review_status(client, user, review_id: str, status: str) -> None:
    resp = client.patch(
        f"/reviews/{review_id}",
        json={"status": status},
        headers=user.auth_headers,
    )
    assert resp.status_code == 200, resp.text


# ─── Feature 1: reviewStatus filter ──────────────────────────────────────────

def test_unreviewed_excludes_records_with_review_queue_entry(client, make_user):
    user = make_user(role="admin")

    rec_with_review = _ingest(client, user, file_path="/a.py")
    rec_without_review = _ingest(client, user, file_path="/b.py")

    _create_review(client, user, rec_with_review)

    uuids = _search(client, user, reviewStatus="unreviewed")
    assert rec_without_review in uuids
    assert rec_with_review not in uuids


def test_pending_returns_only_pending_reviews(client, make_user):
    user = make_user(role="admin")

    rec_pending = _ingest(client, user, file_path="/p.py")
    rec_approved = _ingest(client, user, file_path="/q.py")
    rec_no_review = _ingest(client, user, file_path="/r.py")

    _create_review(client, user, rec_pending)  # stays pending

    review_id = _create_review(client, user, rec_approved)
    _set_review_status(client, user, review_id, "approved")

    uuids = _search(client, user, reviewStatus="pending")
    assert rec_pending in uuids
    assert rec_approved not in uuids
    assert rec_no_review not in uuids


def test_reviewed_returns_approved_and_rejected(client, make_user):
    user = make_user(role="admin")

    rec_approved = _ingest(client, user, file_path="/ap.py")
    rec_rejected = _ingest(client, user, file_path="/rj.py")
    rec_pending = _ingest(client, user, file_path="/pe.py")
    rec_no_review = _ingest(client, user, file_path="/nr.py")

    rid_a = _create_review(client, user, rec_approved)
    _set_review_status(client, user, rid_a, "approved")

    rid_r = _create_review(client, user, rec_rejected)
    _set_review_status(client, user, rid_r, "rejected")

    _create_review(client, user, rec_pending)  # stays pending

    uuids = _search(client, user, reviewStatus="reviewed")
    assert rec_approved in uuids
    assert rec_rejected in uuids
    assert rec_pending not in uuids
    assert rec_no_review not in uuids


def test_invalid_review_status_returns_400(client, make_user):
    user = make_user(role="admin")
    resp = client.post(
        "/search",
        json={"workspaceId": user.workspace_id, "reviewStatus": "nonsense"},
        headers=user.auth_headers,
    )
    assert resp.status_code == 400


# ─── Feature 2: modelFamily filter ───────────────────────────────────────────

def test_model_family_claude_matches_claude_variants(client, make_user):
    user = make_user(role="admin")

    rec_opus = _ingest(client, user, file_path="/c1.py", model_name="claude-opus-4-8")
    rec_haiku = _ingest(client, user, file_path="/c2.py", model_name="Claude-Haiku")
    rec_gpt = _ingest(client, user, file_path="/c3.py", model_name="gpt-4o")

    uuids = _search(client, user, modelFamily="claude")
    assert rec_opus in uuids
    assert rec_haiku in uuids
    assert rec_gpt not in uuids


def test_model_family_case_insensitive(client, make_user):
    user = make_user(role="admin")

    rec = _ingest(client, user, file_path="/ci.py", model_name="GPT-4-turbo")

    uuids_upper = _search(client, user, modelFamily="GPT")
    uuids_lower = _search(client, user, modelFamily="gpt")
    assert rec in uuids_upper
    assert rec in uuids_lower


def test_model_family_does_not_match_other_families(client, make_user):
    user = make_user(role="admin")

    rec_gemini = _ingest(client, user, file_path="/g.py", model_name="gemini-pro")
    rec_claude = _ingest(client, user, file_path="/cl.py", model_name="claude-sonnet-4-6")

    uuids = _search(client, user, modelFamily="claude")
    assert rec_claude in uuids
    assert rec_gemini not in uuids


def test_model_family_and_model_name_both_apply(client, make_user):
    user = make_user(role="admin")

    rec_opus = _ingest(client, user, file_path="/o.py", model_name="claude-opus-4-8")
    rec_haiku = _ingest(client, user, file_path="/h.py", model_name="claude-haiku-4-5")

    # modelFamily=claude AND modelName=opus — only opus should match
    uuids = _search(client, user, modelFamily="claude", modelName="opus")
    assert rec_opus in uuids
    assert rec_haiku not in uuids


# ─── Feature 3: auto-categories at ingest + category filter ──────────────────

def test_auth_file_path_produces_auth_category_tag(client, make_user, db_query):
    user = make_user(role="admin")

    rec_uuid = _ingest(client, user, file_path="/src/auth/login.py", inserted_text="def login(): pass\n")

    async def _tags(session):
        result = await session.execute(
            select(ProvenanceTag.tag).where(
                ProvenanceTag.workspace_id == user.workspace_id,
                ProvenanceTag.record_uuid == rec_uuid,
            )
        )
        return [row[0] for row in result.all()]

    tags = db_query(_tags)
    assert "ai-category:auth" in tags


def test_hardcoded_password_in_auth_path_produces_auth_and_secrets_tags(client, make_user, db_query):
    user = make_user(role="admin")

    code = 'password = "hardcoded_secret_123"\ndef authenticate(): pass\n'
    rec_uuid = _ingest(client, user, file_path="/src/auth/helpers.py", inserted_text=code)

    async def _tags(session):
        result = await session.execute(
            select(ProvenanceTag.tag).where(
                ProvenanceTag.workspace_id == user.workspace_id,
                ProvenanceTag.record_uuid == rec_uuid,
            )
        )
        return [row[0] for row in result.all()]

    tags = db_query(_tags)
    assert "ai-category:auth" in tags, f"Expected ai-category:auth in {tags}"
    assert "ai-category:secrets" in tags, f"Expected ai-category:secrets in {tags}"


def test_category_filter_returns_only_matching_records(client, make_user):
    user = make_user(role="admin")

    # This record's file path hits the auth category
    rec_auth = _ingest(client, user, file_path="/src/auth/middleware.py", inserted_text="pass\n")
    # Plain record — no category tags
    rec_plain = _ingest(client, user, file_path="/src/utils/helpers.py", inserted_text="x = 1\n")

    uuids = _search(client, user, category="auth")
    assert rec_auth in uuids
    assert rec_plain not in uuids


def test_category_filter_sql(client, make_user):
    user = make_user(role="admin")

    sql_code = "SELECT id FROM users WHERE active = 1\n"
    rec_sql = _ingest(client, user, file_path="/repo/data.py", inserted_text=sql_code)
    rec_plain = _ingest(client, user, file_path="/repo/util.py", inserted_text="pass\n")

    uuids = _search(client, user, category="sql")
    assert rec_sql in uuids
    assert rec_plain not in uuids


def test_duplicate_ingest_does_not_double_write_tags(client, make_user, db_query):
    user = make_user(role="admin")
    key = str(uuid_pkg.uuid4())

    payload = {
        "workspaceId": user.workspace_id,
        "filePath": "/src/auth/login.py",
        "insertedText": "pass\n",
        "id": key,
    }
    headers = {**user.auth_headers, "X-Idempotency-Key": key}

    r1 = client.post("/ingest", json=payload, headers=headers)
    assert r1.json()["stored"] is True

    r2 = client.post("/ingest", json=payload, headers=headers)
    assert r2.json()["stored"] is False  # dedup short-circuit

    async def _count_auth_tags(session):
        from sqlalchemy import func
        return await session.scalar(
            select(func.count()).select_from(ProvenanceTag).where(
                ProvenanceTag.workspace_id == user.workspace_id,
                ProvenanceTag.record_uuid == key,
                ProvenanceTag.tag == "ai-category:auth",
            )
        )

    count = db_query(_count_auth_tags)
    assert count == 1, f"Expected exactly 1 auth tag, got {count}"


# ─── Combined query ───────────────────────────────────────────────────────────

def test_combined_model_family_date_range_and_unreviewed(client, make_user):
    from datetime import datetime, timezone

    user = make_user(role="admin")

    # Target: claude model, not in review queue
    rec_target = _ingest(client, user, file_path="/t1.py", model_name="claude-sonnet-4-6")

    # Same workspace, same model, but in review queue — should be excluded
    rec_reviewed_claude = _ingest(client, user, file_path="/t2.py", model_name="claude-haiku-4-5")
    _create_review(client, user, rec_reviewed_claude)

    # Different model — should be excluded by modelFamily
    rec_gpt = _ingest(client, user, file_path="/t3.py", model_name="gpt-4o")

    date_from = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    date_to = datetime(2099, 12, 31, tzinfo=timezone.utc).isoformat()

    uuids = _search(
        client, user,
        modelFamily="claude",
        dateFrom=date_from,
        dateTo=date_to,
        reviewStatus="unreviewed",
    )

    assert rec_target in uuids
    assert rec_reviewed_claude not in uuids
    assert rec_gpt not in uuids


def test_combined_date_range_review_status_and_category(client, make_user):
    """dateFrom + reviewStatus=unreviewed + category=auth all compose correctly."""
    from datetime import datetime, timezone

    user = make_user(role="admin")

    # auth file + unreviewed — should match
    rec_target = _ingest(
        client, user, file_path="/src/auth/login.py", inserted_text="def login(): pass\n"
    )

    # auth file but in review queue — excluded by reviewStatus
    rec_in_review = _ingest(
        client, user, file_path="/src/auth/helpers.py", inserted_text="pass\n"
    )
    _create_review(client, user, rec_in_review)

    # non-auth file, unreviewed — excluded by category
    rec_plain = _ingest(client, user, file_path="/src/utils/math.py", inserted_text="x = 1\n")

    date_from = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    date_to = datetime(2099, 12, 31, tzinfo=timezone.utc).isoformat()

    uuids = _search(
        client, user,
        dateFrom=date_from,
        dateTo=date_to,
        reviewStatus="unreviewed",
        category="auth",
    )

    assert rec_target in uuids
    assert rec_in_review not in uuids
    assert rec_plain not in uuids


# ─── Feature 4: ai_category facet ────────────────────────────────────────────

def test_ai_category_facet_counts(client, make_user):
    user = make_user(role="admin")

    # Two auth records
    _ingest(client, user, file_path="/src/auth/login.py", inserted_text="pass\n")
    _ingest(client, user, file_path="/src/auth/signup.py", inserted_text="pass\n")
    # One sql record
    _ingest(client, user, file_path="/repo/data.py", inserted_text="SELECT id FROM users\n")
    # One plain record — no category tag
    _ingest(client, user, file_path="/src/utils/helpers.py", inserted_text="x = 1\n")

    resp = client.get("/search/facets", headers=user.auth_headers)
    assert resp.status_code == 200, resp.text
    facets = resp.json()

    assert "ai_category" in facets
    by_value = {f["value"]: f["count"] for f in facets["ai_category"]}
    assert by_value.get("auth", 0) >= 2
    assert by_value.get("sql", 0) >= 1
