from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import Settings


class Neo4jLineageService:
    def __init__(self, settings: Settings) -> None:
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as exc:
            raise RuntimeError(
                "neo4j package is not installed. Max mode requires neo4j==5.26.0."
            ) from exc
        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        self._database = settings.neo4j_database

    async def close(self) -> None:
        await self._driver.close()

    async def ensure_constraints(self) -> None:
        query_statements = [
            "CREATE CONSTRAINT ai_block_id IF NOT EXISTS FOR (b:AIGeneratedBlock) REQUIRE b.blockId IS UNIQUE",
            "CREATE CONSTRAINT ai_version_id IF NOT EXISTS FOR (v:ProvenanceBlockVersion) REQUIRE v.versionId IS UNIQUE",
            "CREATE INDEX ai_block_workspace IF NOT EXISTS FOR (b:AIGeneratedBlock) ON (b.workspaceId)",
            "CREATE INDEX ai_version_file IF NOT EXISTS FOR (v:ProvenanceBlockVersion) ON (v.filePath)",
        ]

        async with self._driver.session(database=self._database) as session:
            for statement in query_statements:
                await session.run(statement)

    async def create_initial_lineage_version(
        self,
        *,
        record_uuid: str,
        workspace_id: str,
        file_path: str,
        code: str,
        ast_tokens: list[str],
        timestamp: datetime,
    ) -> str:
        block_id = record_uuid
        version_id = record_uuid

        query = """
        MERGE (b:AIGeneratedBlock {blockId: $blockId})
          ON CREATE SET b.createdAt = datetime($timestampIso),
                        b.workspaceId = $workspaceId,
                        b.deleted = false
          SET b.updatedAt = datetime($timestampIso)

        MERGE (v:ProvenanceBlockVersion {versionId: $versionId})
          SET v.blockId = $blockId,
              v.workspaceId = $workspaceId,
              v.filePath = $filePath,
              v.code = $code,
              v.astTokens = $astTokens,
              v.createdAt = datetime($timestampIso),
              v.updatedAt = datetime($timestampIso),
              v.commitHash = null,
              v.deleted = false

        MERGE (b)-[:HAS_VERSION]->(v)

        WITH b, v
        OPTIONAL MATCH (b)-[oldLatest:LATEST_VERSION]->(:ProvenanceBlockVersion)
        DELETE oldLatest

        MERGE (b)-[:LATEST_VERSION]->(v)
        """

        params = {
            "blockId": block_id,
            "versionId": version_id,
            "workspaceId": workspace_id,
            "filePath": file_path,
            "code": code,
            "astTokens": ast_tokens,
            "timestampIso": timestamp.isoformat(),
        }

        async with self._driver.session(database=self._database) as session:
            await session.run(query, params)

        return version_id

    async def add_file_version(
        self,
        *,
        block_id: str,
        new_version_id: str,
        file_path: str,
        code: str,
        ast_tokens: list[str],
        commit_hash: str | None,
        timestamp: datetime,
    ) -> str:
        """Append a new version to an existing block, linking it to the previous latest version."""
        query = """
        MATCH (b:AIGeneratedBlock {blockId: $blockId})

        OPTIONAL MATCH (b)-[:LATEST_VERSION]->(prev:ProvenanceBlockVersion)

        CREATE (v:ProvenanceBlockVersion {
            versionId: $versionId,
            blockId: $blockId,
            workspaceId: b.workspaceId,
            filePath: $filePath,
            code: $code,
            astTokens: $astTokens,
            commitHash: $commitHash,
            createdAt: datetime($timestampIso),
            updatedAt: datetime($timestampIso),
            deleted: false
        })

        MERGE (b)-[:HAS_VERSION]->(v)

        WITH b, v, prev
        OPTIONAL MATCH (b)-[oldLatest:LATEST_VERSION]->()
        DELETE oldLatest
        MERGE (b)-[:LATEST_VERSION]->(v)
        SET b.updatedAt = datetime($timestampIso)

        WITH v, prev
        FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END |
            MERGE (v)-[:EVOLVED_FROM]->(prev)
        )
        """

        params = {
            "blockId": block_id,
            "versionId": new_version_id,
            "filePath": file_path,
            "code": code,
            "astTokens": ast_tokens,
            "commitHash": commit_hash,
            "timestampIso": timestamp.isoformat(),
        }

        async with self._driver.session(database=self._database) as session:
            await session.run(query, params)

        return new_version_id

    async def delete_lineage_record(self, *, record_uuid: str) -> None:
        """Remove block and version nodes whose ID matches record_uuid.

        Called when a Postgres commit fails after Neo4j write to prevent
        orphaned graph nodes that have no corresponding provenance record.
        """
        query = """
        OPTIONAL MATCH (b:AIGeneratedBlock {blockId: $recordUuid})
        OPTIONAL MATCH (v:ProvenanceBlockVersion {versionId: $recordUuid})
        DETACH DELETE b, v
        """
        async with self._driver.session(database=self._database) as session:
            await session.run(query, {"recordUuid": record_uuid})

    async def soft_delete_block(self, *, block_id: str, timestamp: datetime) -> None:
        """Mark a block and its latest version as deleted (e.g. file was removed)."""
        query = """
        MATCH (b:AIGeneratedBlock {blockId: $blockId})
        SET b.deleted = true, b.deletedAt = datetime($timestampIso)

        WITH b
        OPTIONAL MATCH (b)-[:LATEST_VERSION]->(v:ProvenanceBlockVersion)
        SET v.deleted = true, v.deletedAt = datetime($timestampIso)
        """

        async with self._driver.session(database=self._database) as session:
            await session.run(query, {"blockId": block_id, "timestampIso": timestamp.isoformat()})

    async def get_lineage_chain(self, *, block_id: str) -> list[dict[str, Any]]:
        """Return all versions for a block ordered oldest-first via EVOLVED_FROM chain."""
        query = """
        MATCH (b:AIGeneratedBlock {blockId: $blockId})-[:HAS_VERSION]->(v:ProvenanceBlockVersion)
        RETURN v.versionId AS versionId,
               v.filePath AS filePath,
               v.commitHash AS commitHash,
               v.createdAt AS createdAt,
               v.deleted AS deleted,
               size(v.astTokens) AS tokenCount
        ORDER BY v.createdAt ASC
        """

        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, {"blockId": block_id})
            records = await result.data()

        return [
            {
                "versionId": r["versionId"],
                "filePath": r["filePath"],
                "commitHash": r["commitHash"],
                "createdAt": str(r["createdAt"]) if r["createdAt"] else None,
                "deleted": r["deleted"],
                "tokenCount": r["tokenCount"],
            }
            for r in records
        ]

    async def find_blocks_in_file(
        self, *, workspace_id: str, file_path: str
    ) -> list[dict[str, Any]]:
        """Return all active (non-deleted) blocks for a given file."""
        query = """
        MATCH (b:AIGeneratedBlock {workspaceId: $workspaceId, deleted: false})
              -[:LATEST_VERSION]->(v:ProvenanceBlockVersion {filePath: $filePath, deleted: false})
        RETURN b.blockId AS blockId,
               b.createdAt AS blockCreatedAt,
               b.updatedAt AS blockUpdatedAt,
               v.versionId AS latestVersionId,
               v.commitHash AS latestCommitHash,
               v.createdAt AS versionCreatedAt,
               size(v.astTokens) AS tokenCount
        ORDER BY v.createdAt DESC
        """

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query, {"workspaceId": workspace_id, "filePath": file_path}
            )
            records = await result.data()

        return [
            {
                "blockId": r["blockId"],
                "blockCreatedAt": str(r["blockCreatedAt"]) if r["blockCreatedAt"] else None,
                "blockUpdatedAt": str(r["blockUpdatedAt"]) if r["blockUpdatedAt"] else None,
                "latestVersionId": r["latestVersionId"],
                "latestCommitHash": r["latestCommitHash"],
                "versionCreatedAt": str(r["versionCreatedAt"]) if r["versionCreatedAt"] else None,
                "tokenCount": r["tokenCount"],
            }
            for r in records
        ]
