from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthContext, ensure_workspace_scope, get_current_auth_context
from app.db.session import get_db_session
from app.services.provenance_service import get_provenance_by_uuid


router = APIRouter(prefix="/lineage", tags=["lineage"])
logger = logging.getLogger(__name__)


def _get_neo4j(request: Request):
    """Return the Neo4j service from app state, or raise 503 if unavailable."""
    neo4j_service = getattr(request.app.state, "neo4j_service", None)
    if neo4j_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lineage graph is not enabled on this instance.",
        )
    return neo4j_service


# NOTE: /graph must be declared before /{record_uuid} to avoid the path
# parameter capturing the literal string "graph".

@router.get("/graph")
async def get_lineage_graph(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
    limit: int = Query(100, le=500),
) -> dict:
    """Full lineage subgraph as JSON nodes+edges."""
    ensure_workspace_scope(auth, workspace_id)
    neo4j_service = _get_neo4j(request)

    # Query blocks and their version chains for the workspace
    query = """
    MATCH (b:AIGeneratedBlock {workspaceId: $workspaceId, deleted: false})
          -[:HAS_VERSION]->(v:ProvenanceBlockVersion)
    WITH b, v
    ORDER BY v.createdAt ASC
    WITH b, collect(v) AS versions
    LIMIT $limit
    UNWIND versions AS v
    OPTIONAL MATCH (v)-[:EVOLVED_FROM]->(prev:ProvenanceBlockVersion)
    RETURN b.blockId AS blockId,
           b.createdAt AS blockCreatedAt,
           v.versionId AS versionId,
           v.filePath AS filePath,
           v.createdAt AS versionCreatedAt,
           v.deleted AS deleted,
           size(v.astTokens) AS tokenCount,
           prev.versionId AS evolvedFromVersionId
    """

    async with neo4j_service._driver.session(database=neo4j_service._database) as neo4j_session:
        result = await neo4j_session.run(query, {"workspaceId": workspace_id, "limit": limit})
        records = await result.data()

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_blocks: set[str] = set()
    seen_versions: set[str] = set()

    for r in records:
        block_id = r["blockId"]
        version_id = r["versionId"]

        if block_id and block_id not in seen_blocks:
            seen_blocks.add(block_id)
            nodes.append({
                "id": block_id,
                "type": "block",
                "createdAt": str(r["blockCreatedAt"]) if r["blockCreatedAt"] else None,
            })

        if version_id and version_id not in seen_versions:
            seen_versions.add(version_id)
            nodes.append({
                "id": version_id,
                "type": "version",
                "blockId": block_id,
                "filePath": r["filePath"],
                "createdAt": str(r["versionCreatedAt"]) if r["versionCreatedAt"] else None,
                "deleted": r["deleted"],
                "tokenCount": r["tokenCount"],
            })
            if block_id:
                edges.append({"from": block_id, "to": version_id, "rel": "HAS_VERSION"})

        if r.get("evolvedFromVersionId"):
            edges.append({
                "from": version_id,
                "to": r["evolvedFromVersionId"],
                "rel": "EVOLVED_FROM",
            })

    return {
        "workspaceId": workspace_id,
        "nodes": nodes,
        "edges": edges,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
    }


@router.get("/chain/{file_path:path}")
async def get_file_chain(
    file_path: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
    workspace_id: str = Query(...),
) -> dict:
    """Show how a file evolved across AI insertions."""
    ensure_workspace_scope(auth, workspace_id)
    neo4j_service = _get_neo4j(request)

    blocks = await neo4j_service.find_blocks_in_file(
        workspace_id=workspace_id,
        file_path=file_path,
    )
    return {
        "filePath": file_path,
        "workspaceId": workspace_id,
        "blocks": blocks,
        "blockCount": len(blocks),
    }


@router.get("/{record_uuid}")
async def get_record_lineage(
    record_uuid: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth_context)],
) -> dict:
    """Return the full ancestry chain for a provenance record."""
    neo4j_service = _get_neo4j(request)

    # Verify record exists in this workspace
    record = await get_provenance_by_uuid(session, record_uuid, auth.workspace_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provenance record not found.",
        )

    chain = await neo4j_service.get_lineage_chain(block_id=record_uuid)
    return {
        "recordUuid": record_uuid,
        "workspaceId": auth.workspace_id,
        "filePath": record.file_path,
        "lineageChain": chain,
        "versionCount": len(chain),
    }
