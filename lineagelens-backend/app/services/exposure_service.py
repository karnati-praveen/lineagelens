from __future__ import annotations

"""Service logic for AI Change Exposure Exchange (OpenVEX & OSV)."""

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RecallCampaign, ProvenanceRecord
from app.schemas.exposure import (
    OPENVEX_CONTEXT,
    OSV_SCHEMA_VERSION,
    OpenVexDocument,
    OpenVexStatement,
    OpenVexVulnerability,
    OSVAffected,
    OSVAffectedPackage,
    OSVDocument,
    OSVReference,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def build_openvex_advisory(
    session: AsyncSession,
    workspace_id: str,
    campaign_id: int | None = None,
) -> OpenVexDocument:
    """Build a standard OpenVEX v0.2.0 document from workspace recall campaigns."""
    stmt = select(RecallCampaign).where(RecallCampaign.workspace_id == workspace_id)
    if campaign_id is not None:
        stmt = stmt.where(RecallCampaign.id == campaign_id)
    else:
        stmt = stmt.order_by(desc(RecallCampaign.created_at)).limit(50)

    result = await session.execute(stmt)
    campaigns = result.scalars().all()

    statements: list[OpenVexStatement] = []
    for c in campaigns:
        vuln_id = f"LL-RECALL-{c.id}"
        criteria_summary = str(c.criteria_json or {})
        vuln = OpenVexVulnerability(
            name=vuln_id,
            description=f"AI Model recall campaign targeting criteria: {criteria_summary}",
            aliases=[f"workspace:{workspace_id}:campaign:{c.id}"],
        )

        # Products are the affected record URIs and descendant blast radius
        members = list(c.member_uuids or [])
        blast = list(c.blast_uuids or [])
        all_uuids = sorted(list(set(members + blast)))
        products = [f"pkg:lineagelens/{workspace_id}/record@{u}" for u in all_uuids] if all_uuids else [f"pkg:lineagelens/{workspace_id}#campaign-{c.id}"]

        status = "fixed" if c.status == "closed" else "affected"
        action = (
            "Quarantined and flagged in lineage tree via LineageLens recall campaign."
            if c.status == "open"
            else "Campaign resolved; affected nodes cleared or replaced."
        )

        statements.append(
            OpenVexStatement(
                vulnerability=vuln,
                products=products,
                status=status,
                action_statement=action,
                timestamp=(c.created_at or datetime.now(timezone.utc)).isoformat(),
            )
        )

    doc_id = f"https://lineagelens.local/exposure/openvex/{workspace_id}/{uuid.uuid4().hex[:12]}"
    return OpenVexDocument(
        context=OPENVEX_CONTEXT,
        id=doc_id,
        timestamp=_iso_now(),
        version=1,
        statements=statements,
    )


async def build_osv_advisory(
    session: AsyncSession,
    workspace_id: str,
    campaign_id: int | None = None,
) -> list[OSVDocument]:
    """Build OSV 1.6.0 documents from workspace recall campaigns."""
    stmt = select(RecallCampaign).where(RecallCampaign.workspace_id == workspace_id)
    if campaign_id is not None:
        stmt = stmt.where(RecallCampaign.id == campaign_id)
    else:
        stmt = stmt.order_by(desc(RecallCampaign.created_at)).limit(50)

    result = await session.execute(stmt)
    campaigns = result.scalars().all()

    osv_docs: list[OSVDocument] = []
    for c in campaigns:
        now_str = _iso_now()
        pub_str = (c.created_at or datetime.now(timezone.utc)).isoformat()
        osv_id = f"LL-ADV-{workspace_id[:8].upper()}-{c.id}"

        members = list(c.member_uuids or [])
        affected_items = [
            OSVAffected(
                package=OSVAffectedPackage(
                    ecosystem="LineageLens",
                    name=f"provenance-{u}",
                    purl=f"pkg:lineagelens/{workspace_id}/record@{u}",
                ),
                versions=[u],
            )
            for u in members[:100]  # Cap package list at 100 entries
        ] or [
            OSVAffected(
                package=OSVAffectedPackage(
                    ecosystem="LineageLens",
                    name=f"workspace-{workspace_id}",
                    purl=f"pkg:lineagelens/{workspace_id}",
                )
            )
        ]

        doc = OSVDocument(
            schema_version=OSV_SCHEMA_VERSION,
            id=osv_id,
            modified=now_str,
            published=pub_str,
            summary=f"AI Provenance Recall Campaign #{c.id} ({c.status})",
            details=f"LineageLens identified {c.matched_count} code insertions matching recall criteria {c.criteria_json}. Status is currently {c.status}.",
            affected=affected_items,
            references=[
                OSVReference(type="ADVISORY", url=f"/api/v1/recall/{c.id}")
            ],
        )
        osv_docs.append(doc)

    return osv_docs
