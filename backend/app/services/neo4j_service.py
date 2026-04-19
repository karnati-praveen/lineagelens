from datetime import datetime

from neo4j import AsyncGraphDatabase

from app.core.config import Settings


class Neo4jLineageService:
    def __init__(self, settings: Settings) -> None:
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
                        b.workspaceId = $workspaceId
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
