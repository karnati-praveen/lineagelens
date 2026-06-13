from __future__ import annotations

import json
import logging
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attestation import SignedAttestation, build_attestation, sign_attestation
from app.core.audit import log_audit_event
from app.db.models import Attestation, HumanReviewAttestation

logger = logging.getLogger(__name__)

DepthSignal = Literal["shallow", "adequate", "deep"]

# ─── Depth signal formula (transparent thresholds) ────────────────────────────
#
# Input signals
#   time_per_line  = seconds_on_diff / max(lines_reviewed, 1)
#   comment_count  = inline or PR comments left by the reviewer
#   lines_reviewed = AI-flagged lines the reviewer claims to have seen
#
# Scoring (0–100):
#   time_score     = min(time_per_line / 5.0, 1.0) × 40   → max 40 pts at ≥5 s/line
#   comment_score  = min(comment_count  / 3.0, 1.0) × 30   → max 30 pts at ≥3 comments
#   coverage_score = min(lines_reviewed / 50.0, 1.0) × 30  → max 30 pts at ≥50 lines
#   raw_score      = time_score + comment_score + coverage_score
#
# Implausibly-fast override: time_per_line < 1.0 s → always "shallow"
# (flags rubber-stamp approvals of large diffs, e.g. 3 s for 400 lines).
#
# Bands:
#   shallow   raw_score < 35
#   adequate  35 ≤ raw_score < 70
#   deep      raw_score ≥ 70
# ──────────────────────────────────────────────────────────────────────────────


def compute_depth_signal(
    lines_reviewed: int,
    seconds_on_diff: int,
    comment_count: int,
) -> tuple[DepthSignal, float]:
    """Return (depth_signal, raw_score_0_to_100)."""
    lr = max(lines_reviewed, 1)
    time_per_line = seconds_on_diff / lr

    if time_per_line < 1.0:
        return "shallow", 0.0

    time_score = min(time_per_line / 5.0, 1.0) * 40.0
    comment_score = min(comment_count / 3.0, 1.0) * 30.0
    coverage_score = min(lines_reviewed / 50.0, 1.0) * 30.0
    raw = time_score + comment_score + coverage_score

    if raw >= 70.0:
        return "deep", raw
    if raw >= 35.0:
        return "adequate", raw
    return "shallow", raw


async def record_review(
    session: AsyncSession,
    *,
    workspace_id: str,
    scope_ref: str,
    reviewer_user_id: str,
    lines_reviewed: int,
    seconds_on_diff: int,
    comment_count: int,
    verdict: str,
) -> HumanReviewAttestation:
    """Record a signed human review attestation for a provenance record or PR ref.

    Computes depth_signal from the formula above, signs an Attestation row via
    app.core.attestation (does not duplicate signing logic), persists both rows,
    and writes an audit event.  Caller must commit the session.
    """
    depth_signal, raw_score = compute_depth_signal(lines_reviewed, seconds_on_diff, comment_count)

    statement = build_attestation(
        subject_type="review",
        subject_id=scope_ref,
        claims={
            "reviewer": reviewer_user_id,
            "lines_reviewed": lines_reviewed,
            "seconds_on_diff": seconds_on_diff,
            "comment_count": comment_count,
            "depth_signal": depth_signal,
            "depth_score": round(raw_score, 2),
            "verdict": verdict,
        },
        workspace_id=workspace_id,
    )
    signed: SignedAttestation = sign_attestation(statement)

    att_row = Attestation(
        workspace_id=workspace_id,
        subject_type="review",
        subject_id=scope_ref,
        statement_json=json.dumps(signed.statement, sort_keys=True),
        signature=signed.signature,
        public_key_id=signed.public_key_id,
    )
    session.add(att_row)
    await session.flush()

    hra = HumanReviewAttestation(
        workspace_id=workspace_id,
        scope_ref=scope_ref,
        reviewer_user_id=reviewer_user_id,
        lines_reviewed=lines_reviewed,
        seconds_on_diff=seconds_on_diff,
        comment_count=comment_count,
        depth_signal=depth_signal,
        verdict=verdict,
        attestation_id=att_row.id,
    )
    session.add(hra)

    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=reviewer_user_id,
        action="human_review.attest",
        target_uuid=scope_ref,
        details={
            "depth_signal": depth_signal,
            "depth_score": round(raw_score, 2),
            "verdict": verdict,
            "lines_reviewed": lines_reviewed,
            "seconds_on_diff": seconds_on_diff,
        },
    )

    return hra


async def get_review_status(
    session: AsyncSession,
    *,
    workspace_id: str,
    scope_ref: str,
) -> dict:
    """Return the most recent human review status for scope_ref within workspace.

    Consumed by F1's evaluate_eligibility to close the require_human_review gate:
        status = await get_review_status(session, workspace_id=ws, scope_ref=record_uuid)
        if policy.require_human_review and not status["has_review"]:
            reasons.append("no_human_review")

    Keys: has_review, depth_signal, verdict, reviewer_user_id, lines_reviewed,
          seconds_on_diff, comment_count, attestation_id, created_at.
    """
    result = await session.execute(
        select(HumanReviewAttestation)
        .where(
            HumanReviewAttestation.workspace_id == workspace_id,
            HumanReviewAttestation.scope_ref == scope_ref,
        )
        .order_by(HumanReviewAttestation.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()

    if row is None:
        return {
            "has_review": False,
            "depth_signal": None,
            "verdict": None,
            "reviewer_user_id": None,
            "lines_reviewed": None,
            "seconds_on_diff": None,
            "comment_count": None,
            "attestation_id": None,
            "created_at": None,
        }

    return {
        "has_review": True,
        "depth_signal": row.depth_signal,
        "verdict": row.verdict,
        "reviewer_user_id": row.reviewer_user_id,
        "lines_reviewed": row.lines_reviewed,
        "seconds_on_diff": row.seconds_on_diff,
        "comment_count": row.comment_count,
        "attestation_id": row.attestation_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
