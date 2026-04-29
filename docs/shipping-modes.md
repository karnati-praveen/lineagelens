# Shipping Modes

LineageLens ships from one codebase in three release modes.

## LineageLens Base

- Proxy capture layer with local-only storage. No backend required.
- Records are stored as a JSON file on disk; no services to run.
- Best for single-user and offline workflows.

Artifact target:

- `lineagelens-base-<version>.zip`

## LineageLens Plus

- Proxy capture layer plus shared backend.
- Adds shared ingest, auth, semantic search, and governance dashboard.
- No Neo4j or vector search dependency.

Artifacts:

- `lineagelens-plus-<version>.zip`
- `deploy/docker-compose.plus.yml`
- `deploy/.env.plus.example`
- `docs/native-backend.md`
- `scripts/run-backend-native.ps1`
- `scripts/test-backend-native.ps1`

## LineageLens Max

- Proxy capture layer plus full backend.
- Adds graph lineage (Neo4j), vector search, and full provenance intelligence on top of Plus.

Artifacts:

- `lineagelens-max-<version>.zip`
- `deploy/docker-compose.max.yml`
- `deploy/.env.max.example`
- `docs/native-backend.md`
- `scripts/run-backend-native.ps1`
- `scripts/test-backend-native.ps1`

## Release Helpers

PowerShell helpers:

- `scripts/package-base.ps1`
- `scripts/package-plus.ps1`
- `scripts/package-max.ps1`

NPM wrappers:

- `npm run ship:base`
- `npm run ship:plus`
- `npm run ship:max`

For the exact end-to-end commands, see [SHIP_PRODUCTS_COMMANDS.md](../SHIP_PRODUCTS_COMMANDS.md).
