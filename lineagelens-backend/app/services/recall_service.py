from __future__ import annotations

import hashlib
import json
import re
import uuid as uuid_pkg
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.db.models import ProvenanceRecord, RecallCampaign


_VALID_QUARANTINE_STATUSES = {"active", "flagged", "quarantined", "cleared"}


@dataclass
class BlastRadiusResult:
    """Descendant set plus an explicit coverage state (PART 2 #14).

    A missing Neo4j must not look like "zero descendants". coverage_status:
      checked        — graph was queried and returned descendants.
      checked_empty  — graph was queried; there genuinely are none.
      unavailable    — no graph configured; descendants were NOT checked.
      failed         — the graph query errored; descendants are unknown.
    """
    descendant_uuids: list[str]
    coverage_status: str
    sources_checked: list[str] = field(default_factory=list)
    graph_checkpoint: str | None = None
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "descendantUuids": self.descendant_uuids,
            "coverageStatus": self.coverage_status,
            "sourcesChecked": self.sources_checked,
            "graphCheckpoint": self.graph_checkpoint,
            "limitations": self.limitations,
        }


def compute_member_digest(member_uuids: list[str]) -> str:
    """Stable SHA-256 over the sorted frozen membership set."""
    canonical = json.dumps(sorted(member_uuids), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def find_affected_records(
    session: AsyncSession,
    workspace_id: str,
    *,
    model: str | None = None,
    model_version: str | None = None,
    prompt_pattern_regex: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    record_uuid: str | None = None,
    limit: int = 1000,
) -> list[ProvenanceRecord]:
    """Return provenance records matching the given criteria, workspace-scoped.

    All DB filters are parameterized. prompt_pattern_regex is applied in Python
    after the DB fetch to avoid dialect-specific regex SQL.
    """
    filters: list[Any] = [ProvenanceRecord.workspace_id == workspace_id]

    if record_uuid is not None:
        try:
            parsed = uuid_pkg.UUID(record_uuid)
        except ValueError:
            return []
        filters.append(ProvenanceRecord.uuid == parsed)

    if model is not None and model.strip():
        from sqlalchemy import func
        clean = model.strip().lower()
        filters.append(
            func.lower(func.coalesce(ProvenanceRecord.model_name, "")).like(f"%{clean}%")
        )

    if model_version is not None and model_version.strip():
        from sqlalchemy import func
        clean = model_version.strip().lower()
        filters.append(
            func.lower(func.coalesce(ProvenanceRecord.model_name, "")).like(f"%{clean}%")
        )

    if date_from is not None:
        filters.append(ProvenanceRecord.timestamp_iso >= date_from)

    if date_to is not None:
        filters.append(ProvenanceRecord.timestamp_iso <= date_to)

    stmt = (
        select(ProvenanceRecord)
        .where(and_(*filters))
        .order_by(ProvenanceRecord.timestamp_iso.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    records = list(result.scalars().all())

    if prompt_pattern_regex:
        try:
            compiled = re.compile(prompt_pattern_regex, re.IGNORECASE)
        except re.error:
            return []
        records = [
            r for r in records
            if r.prompt_messages is not None
            and compiled.search(str(r.prompt_messages))
        ]

    return records


async def compute_blast_radius(
    neo4j_service: Any | None,
    record_uuids: list[str],
    workspace_id: str,
) -> BlastRadiusResult:
    """Return descendant record UUIDs (via EVOLVED_FROM lineage) with coverage.

    PART 2 #14: never collapse "graph unavailable" or "query failed" into an
    empty descendant list that reads as "zero descendants". The coverage_status
    distinguishes checked / checked_empty / unavailable / failed.
    """
    if neo4j_service is None:
        return BlastRadiusResult(
            descendant_uuids=[],
            coverage_status="unavailable",
            sources_checked=[],
            limitations=["neo4j_disabled — descendant lineage was not checked"],
        )
    if not record_uuids:
        return BlastRadiusResult(
            descendant_uuids=[],
            coverage_status="checked_empty",
            sources_checked=["neo4j"],
            graph_checkpoint=datetime.now(UTC).isoformat(),
        )

    query = """
    MATCH (seed:ProvenanceBlockVersion)
    WHERE seed.versionId IN $seedIds
    MATCH (desc:ProvenanceBlockVersion)-[:EVOLVED_FROM*1..]->(seed)
    WHERE desc.workspaceId = $workspaceId
      AND NOT desc.versionId IN $seedIds
    RETURN DISTINCT desc.versionId AS versionId
    """
    checkpoint = datetime.now(UTC).isoformat()
    try:
        async with neo4j_service._driver.session(database=neo4j_service._database) as neo_session:
            result = await neo_session.run(
                query,
                {"seedIds": list(record_uuids), "workspaceId": workspace_id},
            )
            rows = await result.data()
        descendants = [r["versionId"] for r in rows if r.get("versionId")]
        return BlastRadiusResult(
            descendant_uuids=descendants,
            coverage_status="checked" if descendants else "checked_empty",
            sources_checked=["neo4j"],
            graph_checkpoint=checkpoint,
        )
    except Exception as exc:
        return BlastRadiusResult(
            descendant_uuids=[],
            coverage_status="failed",
            sources_checked=["neo4j"],
            graph_checkpoint=checkpoint,
            limitations=[f"neo4j_query_failed: {type(exc).__name__}"],
        )


async def open_recall(
    session: AsyncSession,
    workspace_id: str,
    created_by: str,
    criteria_json: dict,
    matched_count: int,
    *,
    member_uuids: list[str] | None = None,
    blast: BlastRadiusResult | None = None,
    criteria_version: str = "1",
) -> RecallCampaign:
    """Open a recall campaign, freezing a signed membership snapshot (PART 2 #13)."""
    members = list(member_uuids or [])
    member_digest = compute_member_digest(members) if members else None

    # Sign the frozen digest so the membership cannot be silently re-scoped later.
    member_signature: str | None = None
    member_public_key_id: str | None = None
    if member_digest is not None:
        try:
            from app.core.attestation import sign_detached
            member_signature, member_public_key_id = sign_detached(member_digest.encode())
        except Exception:  # signing is best-effort; digest still freezes the set
            member_signature = None

    campaign = RecallCampaign(
        workspace_id=workspace_id,
        created_by=created_by,
        criteria_json=criteria_json,
        status="open",
        matched_count=matched_count,
        criteria_version=criteria_version,
        member_uuids=members,
        member_digest=member_digest,
        member_signature=member_signature,
        member_public_key_id=member_public_key_id,
        blast_uuids=list(blast.descendant_uuids) if blast else [],
        blast_coverage_status=blast.coverage_status if blast else None,
        graph_checkpoint=blast.graph_checkpoint if blast else None,
    )
    session.add(campaign)
    await session.flush()
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=created_by,
        action="recall.open",
        target_uuid=str(campaign.id),
        details={
            "matched_count": matched_count,
            "member_digest": member_digest,
            "blast_coverage": campaign.blast_coverage_status,
            "criteria": criteria_json,
        },
    )
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def _flag_records(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    record_uuids: list[str],
    campaign_id: int,
) -> int:
    """Set quarantine_status=flagged on matching records. Returns affected count."""
    if not record_uuids:
        return 0

    parsed_uuids: list[uuid_pkg.UUID] = []
    for uid in record_uuids:
        try:
            parsed_uuids.append(uuid_pkg.UUID(uid))
        except ValueError:
            continue

    if not parsed_uuids:
        return 0

    stmt = (
        update(ProvenanceRecord)
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
            ProvenanceRecord.uuid.in_(parsed_uuids),
        )
        .values(
            quarantine_status="flagged",
            quarantine_recall_id=campaign_id,
        )
    )
    result = await session.execute(stmt)
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="recall.flag",
        target_uuid=str(campaign_id),
        details={"flagged_count": result.rowcount},
    )
    return result.rowcount


async def quarantine_records(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    campaign_id: int,
    record_uuids: list[str],
    blast_uuids: list[str],
) -> int:
    """Set quarantine_status=quarantined for all matched + blast-radius records."""
    all_uuids = list(dict.fromkeys(record_uuids + blast_uuids))
    if not all_uuids:
        return 0

    parsed: list[uuid_pkg.UUID] = []
    for uid in all_uuids:
        try:
            parsed.append(uuid_pkg.UUID(uid))
        except ValueError:
            continue

    if not parsed:
        return 0

    now = datetime.now(UTC)
    stmt = (
        update(ProvenanceRecord)
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
            ProvenanceRecord.uuid.in_(parsed),
        )
        .values(
            quarantine_status="quarantined",
            quarantine_recall_id=campaign_id,
            quarantined_at=now,
        )
    )
    result = await session.execute(stmt)
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="recall.quarantine",
        target_uuid=str(campaign_id),
        details={
            "quarantined_count": result.rowcount,
            "blast_count": len(blast_uuids),
        },
    )
    await session.commit()
    return result.rowcount


async def clear_records(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    campaign_id: int,
) -> int:
    """Set quarantine_status=cleared for all records belonging to this campaign."""
    stmt = (
        update(ProvenanceRecord)
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
            ProvenanceRecord.quarantine_recall_id == campaign_id,
        )
        .values(quarantine_status="cleared")
    )
    result = await session.execute(stmt)
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="recall.clear",
        target_uuid=str(campaign_id),
        details={"cleared_count": result.rowcount},
    )
    await session.commit()
    return result.rowcount


async def close_recall(
    session: AsyncSession,
    workspace_id: str,
    user_id: str,
    campaign: RecallCampaign,
) -> RecallCampaign:
    campaign.status = "closed"
    campaign.closed_at = datetime.now(UTC)
    await log_audit_event(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        action="recall.close",
        target_uuid=str(campaign.id),
        details={"matched_count": campaign.matched_count},
    )
    await session.commit()
    await session.refresh(campaign)
    return campaign


def _campaign_to_dict(c: RecallCampaign) -> dict:
    return {
        "id": c.id,
        "workspaceId": c.workspace_id,
        "createdBy": c.created_by,
        "criteriaJson": c.criteria_json,
        "criteriaVersion": c.criteria_version,
        "status": c.status,
        "matchedCount": c.matched_count,
        # Frozen, signed membership snapshot (PART 2 #13).
        "memberUuids": c.member_uuids,
        "memberDigest": c.member_digest,
        "memberSignature": c.member_signature,
        "memberPublicKeyId": c.member_public_key_id,
        "blastUuids": c.blast_uuids,
        "blastCoverageStatus": c.blast_coverage_status,
        "graphCheckpoint": c.graph_checkpoint,
        "createdAt": c.created_at.isoformat(),
        "closedAt": c.closed_at.isoformat() if c.closed_at else None,
    }
