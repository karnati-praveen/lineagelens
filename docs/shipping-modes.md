# Shipping Modes

LineageLens ships from one codebase in three release modes.

## LineageLens Base

- Extension plus optional self-hosted backend (bundled for convenience).
- Default mode (`aiCodeProvenance.mode = local`) stores data locally; no backend setup required.
- Best for single-user and offline workflows.

Artifact target:

- `lineagelens-base-<version>.zip`

## LineageLens Plus

- Extension plus backend-basic.
- Shared ingest, auth, search, and dashboard.
- No Neo4j dependency.

Artifacts:

- `lineagelens-plus-<version>.zip`
- `deploy/docker-compose.plus.yml`
- `deploy/.env.example`
- `docs/native-backend.md`
- `scripts/run-backend-native.ps1`
- `scripts/test-backend-native.ps1`

## LineageLens Max

- Extension plus backend-full.
- Shared ingest, auth, search, dashboard, vector search, and Neo4j lineage.

Artifacts:

- `lineagelens-max-<version>.zip`
- `deploy/docker-compose.max.yml`
- `deploy/.env.example`
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
