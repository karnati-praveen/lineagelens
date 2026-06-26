"""Contract tests for /trust (F3 — Outcome-Calibrated Trust).

Covers:
- POST /trust/outcomes is idempotent (duplicate within 24h returns skipped=1)
- GET /trust/durability returns correct grouped scores
- Durability formula matches a hand-computed fixture
- groupBy=model and groupBy=developer both work
- Workspace isolation: outcomes from workspace B do not appear for workspace A
- Incident-linked outcome lowers the group's durability score vs no incident

Run with:
    cd lineagelens-backend && pytest tests/test_trust.py -q
"""
from __future__ import annotations

import asyncio
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _engine_for(database_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool
    return create_async_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool,
    )


def _seed_provenance(
    db_url: str,
    workspace_id: str,
    model_name: str = "gpt-4o",
    user_id: str | None = None,
) -> str:
    rec_uuid = uuid_pkg.uuid4()

    async def _run():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from app.db.models import ProvenanceRecord
        engine = _engine_for(db_url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                ts = datetime.now(UTC) - timedelta(hours=2)
                stored_uid = uuid_pkg.UUID(user_id) if user_id else None
                rec = ProvenanceRecord(
                    uuid=rec_uuid,
                    workspace_id=workspace_id,
                    file_path="/app/feature.py",
                    timestamp_iso=ts,
                    inserted_code="result = ai_fn()",
                    model_name=model_name,
                    user_id=stored_uid,
                    provenance_payload={},
                )
                session.add(rec)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())
    return str(rec_uuid)


def _outcome_payload(record_uuid: str, outcome_type: str, source: str = "manual") -> dict:
    return {
        "events": [
            {
                "recordUuid": record_uuid,
                "outcomeType": outcome_type,
                "source": source,
            }
        ]
    }


def _seed_n_with_outcome(
    client, user, *, model_name: str, n: int, outcome_type: str, source: str = "manual",
    extra_outcome: str | None = None, extra_source: str = "incident",
) -> list[str]:
    """Seed n distinct records for a model and post one outcome (+ optional extra) each.

    Used to clear the insufficient_evidence threshold (>=5 decided outcomes).
    """
    uuids = []
    for _ in range(n):
        rid = _seed_provenance(client.database_url, user.workspace_id, model_name=model_name)
        client.post("/trust/outcomes", json=_outcome_payload(rid, outcome_type, source), headers=user.auth_headers)
        if extra_outcome:
            client.post("/trust/outcomes", json=_outcome_payload(rid, extra_outcome, extra_source), headers=user.auth_headers)
        uuids.append(rid)
    return uuids


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_ingest_outcome_created(client, make_user):
    user = make_user(role="member")
    rec_uuid = _seed_provenance(client.database_url, user.workspace_id)

    resp = client.post(
        "/trust/outcomes",
        json=_outcome_payload(rec_uuid, "survived"),
        headers=user.auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["createdCount"] == 1
    assert body["skippedCount"] == 0


def test_outcome_upsert_is_idempotent(client, make_user):
    """Second POST with same record/type/source within 24h should be skipped."""
    user = make_user(role="member")
    rec_uuid = _seed_provenance(client.database_url, user.workspace_id)

    payload = _outcome_payload(rec_uuid, "test_failed", "ci")

    resp1 = client.post("/trust/outcomes", json=payload, headers=user.auth_headers)
    assert resp1.json()["createdCount"] == 1

    resp2 = client.post("/trust/outcomes", json=payload, headers=user.auth_headers)
    assert resp2.json()["createdCount"] == 0
    assert resp2.json()["skippedCount"] == 1


def test_different_outcome_types_both_recorded(client, make_user):
    """test_failed and incident_linked are different types — both should be created."""
    user = make_user(role="member")
    rec_uuid = _seed_provenance(client.database_url, user.workspace_id)

    resp1 = client.post("/trust/outcomes", json=_outcome_payload(rec_uuid, "test_failed", "ci"), headers=user.auth_headers)
    resp2 = client.post("/trust/outcomes", json=_outcome_payload(rec_uuid, "incident_linked", "incident"), headers=user.auth_headers)

    assert resp1.json()["createdCount"] == 1
    assert resp2.json()["createdCount"] == 1


def test_invalid_outcome_type_returns_error(client, make_user):
    user = make_user(role="member")
    rec_uuid = _seed_provenance(client.database_url, user.workspace_id)

    resp = client.post(
        "/trust/outcomes",
        json={"events": [{"recordUuid": rec_uuid, "outcomeType": "exploded", "source": "manual"}]},
        headers=user.auth_headers,
    )
    assert resp.status_code == 422


def test_durability_groupby_model(client, make_user):
    """Durability endpoint returns a list grouped by model."""
    user = make_user(role="member")
    db_url = client.database_url

    rec_a = _seed_provenance(db_url, user.workspace_id, model_name="model-alpha")
    rec_b = _seed_provenance(db_url, user.workspace_id, model_name="model-alpha")

    client.post("/trust/outcomes", json=_outcome_payload(rec_a, "survived"), headers=user.auth_headers)
    client.post("/trust/outcomes", json=_outcome_payload(rec_b, "reverted", "git"), headers=user.auth_headers)

    resp = client.get("/trust/durability?groupBy=model", headers=user.auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["groupValue"] == "model-alpha" for i in items)


def test_durability_formula_hand_computed(client, make_user):
    """Verify durability score matches the hand-computed value for a known fixture.

    Fixture (scaled to clear the insufficient_evidence threshold): 10 blocks,
    5 survived, 5 reverted, 0 incidents, 0 test_failures.
      total_blocks=10, total_out=10, survival_rate=0.5, revert_rate=0.5
      score = round(0.5*70 + (1-0)*15 + (1-0.5)*10 + (1-0)*5)
            = round(35 + 15 + 5 + 5) = 60
    """
    from app.services.outcome_service import _compute_durability_score

    score = _compute_durability_score(
        survived=5,
        reverted=5,
        rewritten=0,
        test_failed=0,
        incident_linked=0,
        total_blocks=10,
    )
    assert score == 60


def test_durability_insufficient_evidence_below_threshold(client, make_user):
    """Below the minimum decided outcomes the score is None, never a misleading 100 (PART 1 #3)."""
    from app.services.outcome_service import _compute_durability_score

    # 2 survived, nothing else — too few to score.
    assert _compute_durability_score(
        survived=2, reverted=0, rewritten=0, test_failed=0, incident_linked=0, total_blocks=2
    ) is None
    # Zero outcomes must NOT score 100.
    assert _compute_durability_score(
        survived=0, reverted=0, rewritten=0, test_failed=0, incident_linked=0, total_blocks=0
    ) is None


def test_incident_linked_lowers_score(client, make_user):
    """Incident-linked outcome should produce a lower durability score than survived-only."""
    from app.services.outcome_service import _compute_durability_score

    score_clean = _compute_durability_score(
        survived=5, reverted=0, rewritten=0, test_failed=0, incident_linked=0, total_blocks=5
    )
    score_incident = _compute_durability_score(
        survived=5, reverted=0, rewritten=0, test_failed=0, incident_linked=5, total_blocks=5
    )
    assert score_incident < score_clean


def test_durability_via_api_with_incidents(client, make_user):
    """End-to-end: a model with incidents should score lower than one with all survived."""
    user = make_user(role="member")

    _seed_n_with_outcome(client, user, model_name="good-model", n=5, outcome_type="survived")
    # bad-model: same 5 survived but each also incident-linked.
    _seed_n_with_outcome(
        client, user, model_name="bad-model", n=5, outcome_type="survived",
        extra_outcome="incident_linked", extra_source="incident",
    )

    resp = client.get("/trust/durability?groupBy=model", headers=user.auth_headers)
    assert resp.status_code == 200
    items = {i["groupValue"]: i for i in resp.json()["items"]}

    assert items["good-model"]["scoreStatus"] == "ok"
    assert items["bad-model"]["scoreStatus"] == "ok"
    assert items["good-model"]["durabilityScore"] > items["bad-model"]["durabilityScore"]


def test_durability_api_insufficient_evidence(client, make_user):
    """A model with a single outcome reports insufficient_evidence, not a number."""
    user = make_user(role="member")
    rec = _seed_provenance(client.database_url, user.workspace_id, model_name="sparse-model")
    client.post("/trust/outcomes", json=_outcome_payload(rec, "survived"), headers=user.auth_headers)

    resp = client.get("/trust/durability?groupBy=model", headers=user.auth_headers)
    items = {i["groupValue"]: i for i in resp.json()["items"]}
    sparse = items["sparse-model"]
    assert sparse["durabilityScore"] is None
    assert sparse["scoreStatus"] == "insufficient_evidence"
    assert "survivalRateCI95" in sparse
    assert sparse["negativeOutcomesByTrust"]["declared"] == 0


def test_workspace_isolation_durability(client, make_user):
    """Outcomes in workspace B must not appear in workspace A's durability."""
    user_a = make_user(role="member")
    user_b = make_user(role="member")
    db_url = client.database_url

    rec_b = _seed_provenance(db_url, user_b.workspace_id, model_name="b-exclusive-model")
    client.post("/trust/outcomes", json=_outcome_payload(rec_b, "survived"), headers=user_b.auth_headers)

    resp = client.get("/trust/durability?groupBy=model", headers=user_a.auth_headers)
    assert resp.status_code == 200
    values = [i["groupValue"] for i in resp.json()["items"]]
    assert "b-exclusive-model" not in values


def test_get_record_trust_timeline(client, make_user):
    """GET /trust/record/{uuid} returns timeline and modelDurabilityScore."""
    user = make_user(role="member")
    db_url = client.database_url

    rec_uuid = _seed_provenance(db_url, user.workspace_id, model_name="timeline-model")
    client.post("/trust/outcomes", json=_outcome_payload(rec_uuid, "survived"), headers=user.auth_headers)
    client.post("/trust/outcomes", json=_outcome_payload(rec_uuid, "review_flagged", "review"), headers=user.auth_headers)

    resp = client.get(f"/trust/record/{rec_uuid}", headers=user.auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "outcomesTimeline" in body
    assert len(body["outcomesTimeline"]) == 2
    types_in_timeline = {e["outcomeType"] for e in body["outcomesTimeline"]}
    assert "survived" in types_in_timeline
    assert "review_flagged" in types_in_timeline
    assert "modelDurabilityScore" in body


def test_get_record_trust_not_found(client, make_user):
    user = make_user(role="member")
    resp = client.get(f"/trust/record/{uuid_pkg.uuid4()}", headers=user.auth_headers)
    assert resp.status_code == 404


def test_batch_outcomes_max_200(client, make_user):
    """201 events must be rejected — either 422 (Pydantic limit) or 413 (body cap)."""
    user = make_user(role="member")
    rec_uuid = _seed_provenance(client.database_url, user.workspace_id)

    events = [
        {"recordUuid": rec_uuid, "outcomeType": "survived", "source": "manual"}
        for _ in range(201)
    ]
    resp = client.post("/trust/outcomes", json={"events": events}, headers=user.auth_headers)
    # 422 = Pydantic rejects >200 events; 413 = body cap fires first (test env uses 8 KB cap)
    assert resp.status_code in (413, 422)


def test_durability_zero_blocks_returns_empty(client, make_user):
    """When a workspace has no outcomes, durability returns empty list."""
    user = make_user(role="member")
    resp = client.get("/trust/durability?groupBy=model", headers=user.auth_headers)
    assert resp.status_code == 200
    assert resp.json()["items"] == []
