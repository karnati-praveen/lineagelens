from __future__ import annotations

"""Outcome-Calibrated Trust service (F3).

Durability score formula (0–100):
  Let:
    terminal     = reverted + rewritten_by_human  (blocks with a decided fate)
    survived     = survived count
    total_out    = terminal + survived  (denominator for survival_rate)
    survival_rate = survived / total_out  if total_out > 0 else 1.0
    incident_rate = incident_linked / total_blocks
    revert_rate   = terminal / total_blocks
    test_fail_rate= test_failed / total_blocks

  score = max(0, min(100, round(
      survival_rate * 70
      + (1 - incident_rate) * 15
      + (1 - revert_rate)   * 10
      + (1 - test_fail_rate)* 5
  )))

  Interpretation: 100 = no negative outcomes, fully survived.
  Incident linkage is penalised most heavily (up to -15 from the 15-pt band),
  then reverts/rewrites (-10), then test failures (-5).

  A group with fewer than _MIN_DECIDED_OUTCOMES decided outcomes scores None
  (scoreStatus="insufficient_evidence"), NOT 100 — absence of observed failure
  is not evidence of durability (PART 1 #3). Survival is reported as a rate
  *with* a Wilson 95% CI, and negative outcomes are broken down by source trust
  (PART 1 #4).
"""

import math
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.db.models import ProvenanceRecord, RecordOutcome


_VALID_OUTCOME_TYPES = frozenset(
    {"reverted", "rewritten_by_human", "test_failed", "incident_linked", "review_flagged", "survived"}
)
_VALID_SOURCES = frozenset({"git", "ci", "incident", "review", "manual"})
_VALID_GROUP_BY = frozenset({"model", "prompt_pattern", "developer"})

_DEDUP_WINDOW_HOURS = 24

# PART 1 #3 — a durability score is meaningless without enough observed outcomes.
# Below this many decided outcomes we report `insufficient_evidence`, never 100.
_MIN_DECIDED_OUTCOMES = 5

# PART 1 #4 — outcomes carry no cryptographic source proof, so rank by how
# directly observed the source is. A self-declared "manual" survival is far
# weaker evidence than a CI/git-observed revert.
_SOURCE_TRUST = {
    "ci": "observed",
    "git": "observed",
    "incident": "corroborated",
    "review": "corroborated",
    "manual": "declared",
}


def source_trust(source: str) -> str:
    """Map an outcome source to its evidence-trust tier (observed/corroborated/declared)."""
    return _SOURCE_TRUST.get(source, "declared")


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score 95% confidence interval for a survival proportion.

    Honest uncertainty: a 1/1 survival is not the same as 50/50, and the CI
    width makes the difference visible instead of collapsing to a point score.
    """
    if n <= 0:
        return None
    phat = successes / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


async def record_outcome(
    session: AsyncSession,
    *,
    workspace_id: str,
    record_uuid: str,
    outcome_type: str,
    source: str,
    observed_at: datetime | None = None,
    detail_json: dict | None = None,
    user_id: str | None = None,
) -> tuple[RecordOutcome, bool]:
    """Idempotent upsert of an outcome event.

    Returns (outcome, created) where created=False means a duplicate was found.
    Deduplicates on (record_uuid, outcome_type, source) within a 24-hour window.
    """
    if outcome_type not in _VALID_OUTCOME_TYPES:
        raise ValueError(f"Invalid outcome_type: {outcome_type!r}")
    if source not in _VALID_SOURCES:
        raise ValueError(f"Invalid source: {source!r}")

    ts = observed_at or datetime.now(UTC)
    window_start = ts - timedelta(hours=_DEDUP_WINDOW_HOURS)

    existing_stmt = select(RecordOutcome).where(
        RecordOutcome.workspace_id == workspace_id,
        RecordOutcome.record_uuid == record_uuid,
        RecordOutcome.outcome_type == outcome_type,
        RecordOutcome.source == source,
        RecordOutcome.observed_at >= window_start,
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return existing, False

    outcome = RecordOutcome(
        record_uuid=record_uuid,
        workspace_id=workspace_id,
        outcome_type=outcome_type,
        detail_json=detail_json,
        observed_at=ts,
        source=source,
    )
    session.add(outcome)
    await session.flush()
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="outcome.record",
        target_uuid=record_uuid,
        details={"outcome_type": outcome_type, "source": source},
    )
    await session.commit()
    await session.refresh(outcome)
    return outcome, True


def _compute_durability_score(
    survived: int,
    reverted: int,
    rewritten: int,
    test_failed: int,
    incident_linked: int,
    total_blocks: int,
) -> int | None:
    """Durability score (0–100), or None when there is insufficient evidence.

    PART 1 #3: a group with no decided outcomes used to score 100 ("looks
    perfect"). That is false assurance — it means "we have not observed
    anything", not "nothing went wrong". We now return None below a minimum
    number of decided outcomes; callers surface `insufficient_evidence`.
    """
    terminal = reverted + rewritten
    decided = terminal + survived
    if total_blocks == 0 or decided < _MIN_DECIDED_OUTCOMES:
        return None

    total_out = terminal + survived
    survival_rate = survived / total_out if total_out > 0 else 1.0
    incident_rate = incident_linked / total_blocks
    revert_rate = terminal / total_blocks
    test_fail_rate = test_failed / total_blocks

    raw = (
        survival_rate * 70
        + (1.0 - incident_rate) * 15
        + (1.0 - revert_rate) * 10
        + (1.0 - test_fail_rate) * 5
    )
    return max(0, min(100, round(raw)))


async def compute_durability(
    session: AsyncSession,
    workspace_id: str,
    *,
    group_by: str = "model",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return durability leaderboard grouped by model | prompt_pattern | developer.

    Each entry contains:
      groupValue, totalBlocks, survivedCount, revertedCount, rewrittenCount,
      testFailedCount, incidentLinkedCount, reviewFlaggedCount,
      survivalRate, incidentRate, medianTimeToRevert (seconds | null),
      durabilityScore (0–100).
    """
    if group_by not in _VALID_GROUP_BY:
        raise ValueError(f"group_by must be one of {sorted(_VALID_GROUP_BY)}")

    filters: list[Any] = [
        RecordOutcome.workspace_id == workspace_id,
    ]
    if date_from:
        filters.append(RecordOutcome.observed_at >= date_from)
    if date_to:
        filters.append(RecordOutcome.observed_at <= date_to)

    outcomes_stmt = (
        select(RecordOutcome)
        .where(and_(*filters))
    )
    outcomes_result = await session.execute(outcomes_stmt)
    all_outcomes: list[RecordOutcome] = list(outcomes_result.scalars().all())

    if not all_outcomes:
        return []

    # Collect all record UUIDs so we can join to ProvenanceRecord for group key.
    all_uuids = list({o.record_uuid for o in all_outcomes})

    import uuid as _uuid_mod

    parsed_uuids: list[_uuid_mod.UUID] = []
    for uid in all_uuids:
        try:
            parsed_uuids.append(_uuid_mod.UUID(uid))
        except ValueError:
            continue

    prov_stmt = select(ProvenanceRecord).where(
        ProvenanceRecord.workspace_id == workspace_id,
        ProvenanceRecord.uuid.in_(parsed_uuids),
    )
    prov_result = await session.execute(prov_stmt)
    all_records: list[ProvenanceRecord] = list(prov_result.scalars().all())

    # Build lookup: record_uuid (str, no dashes) → ProvenanceRecord
    rec_by_uuid: dict[str, ProvenanceRecord] = {}
    for r in all_records:
        rec_by_uuid[str(r.uuid).replace("-", "")] = r
        rec_by_uuid[str(r.uuid)] = r

    def _group_key(record_uuid: str) -> str:
        rec = rec_by_uuid.get(record_uuid) or rec_by_uuid.get(record_uuid.replace("-", ""))
        if rec is None:
            return "__unknown__"
        if group_by == "model":
            return rec.model_name or "__unknown__"
        if group_by == "developer":
            return str(rec.user_id) if rec.user_id else "__unknown__"
        # prompt_pattern: first 80 chars of prompt as key
        if rec.prompt_messages:
            text = str(rec.prompt_messages)[:80].strip()
            return text if text else "__unknown__"
        return "__unknown__"

    # Group outcomes
    from collections import defaultdict
    groups: dict[str, list[RecordOutcome]] = defaultdict(list)
    for o in all_outcomes:
        groups[_group_key(o.record_uuid)].append(o)

    results = []
    for group_value, outcomes in sorted(groups.items()):
        block_uuids = {o.record_uuid for o in outcomes}
        total_blocks = len(block_uuids)

        by_type: dict[str, list[RecordOutcome]] = defaultdict(list)
        for o in outcomes:
            by_type[o.outcome_type].append(o)

        survived = len({o.record_uuid for o in by_type.get("survived", [])})
        reverted = len({o.record_uuid for o in by_type.get("reverted", [])})
        rewritten = len({o.record_uuid for o in by_type.get("rewritten_by_human", [])})
        test_failed = len({o.record_uuid for o in by_type.get("test_failed", [])})
        incident_linked = len({o.record_uuid for o in by_type.get("incident_linked", [])})
        review_flagged = len({o.record_uuid for o in by_type.get("review_flagged", [])})

        # Median time-to-revert: difference between ingest timestamp and revert observed_at
        ttr_seconds: list[float] = []
        for o in by_type.get("reverted", []) + by_type.get("rewritten_by_human", []):
            rec = rec_by_uuid.get(o.record_uuid)
            if rec and rec.timestamp_iso:
                ingest_ts = rec.timestamp_iso
                if ingest_ts.tzinfo is None:
                    ingest_ts = ingest_ts.replace(tzinfo=UTC)
                obs = o.observed_at
                if obs.tzinfo is None:
                    obs = obs.replace(tzinfo=UTC)
                delta = (obs - ingest_ts).total_seconds()
                if delta >= 0:
                    ttr_seconds.append(delta)

        median_ttr = statistics.median(ttr_seconds) if ttr_seconds else None

        terminal = reverted + rewritten
        total_out = terminal + survived
        # Honest uncertainty: only report a point survival_rate alongside its CI,
        # and don't pretend an empty sample is 100% survival.
        survival_rate = round(survived / total_out, 4) if total_out > 0 else None
        survival_ci = _wilson_interval(survived, total_out)
        incident_rate = round(incident_linked / total_blocks, 4)

        score = _compute_durability_score(
            survived=survived,
            reverted=reverted,
            rewritten=rewritten,
            test_failed=test_failed,
            incident_linked=incident_linked,
            total_blocks=total_blocks,
        )
        score_status = "ok" if score is not None else "insufficient_evidence"

        # Negative outcomes ranked by source trust (PART 1 #4). A self-declared
        # ("manual") revert is weaker evidence than a CI/git-observed one.
        negative_by_trust = {"observed": 0, "corroborated": 0, "declared": 0}
        for otype in ("reverted", "rewritten_by_human", "test_failed", "incident_linked"):
            for o in by_type.get(otype, []):
                negative_by_trust[source_trust(o.source)] += 1

        observed_times = [o.observed_at for o in outcomes if o.observed_at]
        observation_window = {
            "from": min(observed_times).isoformat() if observed_times else None,
            "to": max(observed_times).isoformat() if observed_times else None,
        }

        results.append({
            "groupBy": group_by,
            "groupValue": group_value,
            "totalBlocks": total_blocks,
            "survivedCount": survived,
            "revertedCount": reverted,
            "rewrittenCount": rewritten,
            "testFailedCount": test_failed,
            "incidentLinkedCount": incident_linked,
            "reviewFlaggedCount": review_flagged,
            "decidedOutcomes": total_out,
            "survivalRate": survival_rate,
            "survivalRateCI95": list(survival_ci) if survival_ci else None,
            "incidentRate": incident_rate,
            "medianTimeToRevertSeconds": median_ttr,
            "durabilityScore": score,
            "scoreStatus": score_status,
            "minDecidedOutcomesForScore": _MIN_DECIDED_OUTCOMES,
            "negativeOutcomesByTrust": negative_by_trust,
            "observationWindow": observation_window,
        })

    # Sort best-first; groups with insufficient evidence (score=None) sort last.
    results.sort(key=lambda x: x["durabilityScore"] if x["durabilityScore"] is not None else -1, reverse=True)
    return results


async def ingest_git_outcome(
    session: AsyncSession,
    *,
    workspace_id: str,
    record_uuid: str,
    outcome_type: str,
    observed_at: datetime | None = None,
    detail_json: dict | None = None,
    user_id: str | None = None,
) -> tuple[RecordOutcome, bool]:
    """Map a git-derived signal (revert/rewrite by human) into a RecordOutcome.

    Validates that the record exists and belongs to the workspace before recording.
    outcome_type must be 'reverted' or 'rewritten_by_human'.
    """
    if outcome_type not in {"reverted", "rewritten_by_human"}:
        raise ValueError("ingest_git_outcome only accepts reverted or rewritten_by_human")

    return await record_outcome(
        session,
        workspace_id=workspace_id,
        record_uuid=record_uuid,
        outcome_type=outcome_type,
        source="git",
        observed_at=observed_at,
        detail_json=detail_json,
        user_id=user_id,
    )


async def list_outcomes_for_workspace(
    session: AsyncSession,
    workspace_id: str,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict[str, Any]]:
    """Raw outcome events for a workspace (not the aggregated leaderboard).

    Used by the evidence capsule (PART 5 #51) to bundle outcome evidence
    alongside the records/policies it relates to.
    """
    filters: list[Any] = [RecordOutcome.workspace_id == workspace_id]
    if date_from:
        filters.append(RecordOutcome.observed_at >= date_from)
    if date_to:
        filters.append(RecordOutcome.observed_at <= date_to)
    result = await session.execute(
        select(RecordOutcome).where(and_(*filters)).order_by(RecordOutcome.observed_at.asc())
    )
    return [
        {
            "id": o.id,
            "recordUuid": o.record_uuid,
            "outcomeType": o.outcome_type,
            "source": o.source,
            "sourceTrust": source_trust(o.source),
            "observedAt": o.observed_at.isoformat(),
            "detailJson": o.detail_json,
        }
        for o in result.scalars().all()
    ]


async def get_record_outcome_timeline(
    session: AsyncSession,
    workspace_id: str,
    record_uuid: str,
) -> list[dict[str, Any]]:
    stmt = (
        select(RecordOutcome)
        .where(
            RecordOutcome.workspace_id == workspace_id,
            RecordOutcome.record_uuid == record_uuid,
        )
        .order_by(RecordOutcome.observed_at.asc())
    )
    result = await session.execute(stmt)
    outcomes = result.scalars().all()
    return [
        {
            "id": o.id,
            "outcomeType": o.outcome_type,
            "source": o.source,
            "sourceTrust": source_trust(o.source),
            "observedAt": o.observed_at.isoformat(),
            "detailJson": o.detail_json,
        }
        for o in outcomes
    ]
