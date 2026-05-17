# Shipping Modes

LineageLens ships from one codebase in four release modes.

## LineageLens Base

- VS Code extension only. No proxy, no backend, no services required.
- Records stored locally as JSON in VS Code global state.
- Best for individual developers and offline/air-gapped environments.

Artifact target:

- `lineagelens-base-<version>.vsix` (VS Code extension)

## LineageLens Lite

- Single Docker container, SQLite storage. No Postgres required.
- First-boot setup wizard — admin account created in the browser.
- Transparent proxy capture at `localhost:8788` with all 11 adapter detectors.
- Best for small teams on a $5 VPS or a spare laptop.

Artifacts:

- `lineagelens-lite-<version>.zip`
- `lineagelens-deploy/docker-compose.lite.yml`
- `lineagelens-deploy/.env.lite.example`
- `lineagelens-scripts/quickstart-lite.sh`

## LineageLens Plus

- Proxy capture layer plus shared backend.
- Adds shared ingest, auth, semantic search, and governance dashboard.
- No Neo4j or vector search dependency.

Artifacts:

- `lineagelens-plus-<version>.zip`
- `lineagelens-deploy/docker-compose.plus.yml`
- `lineagelens-deploy/.env.plus.example`
- `docs/native-backend.md`
- `lineagelens-scripts/run-backend-native.ps1`
- `lineagelens-scripts/test-backend-native.ps1`

## LineageLens Max

- Proxy capture layer plus full backend.
- Adds graph lineage (Neo4j), vector search, and full provenance intelligence on top of Plus.

Artifacts:

- `lineagelens-max-<version>.zip`
- `lineagelens-deploy/docker-compose.max.yml`
- `lineagelens-deploy/.env.max.example`
- `docs/native-backend.md`
- `lineagelens-scripts/run-backend-native.ps1`
- `lineagelens-scripts/test-backend-native.ps1`

## Release Helpers

PowerShell helpers:

- `lineagelens-scripts/package-base.ps1`
- `lineagelens-scripts/package-lite.ps1`
- `lineagelens-scripts/package-plus.ps1`
- `lineagelens-scripts/package-max.ps1`
- `lineagelens-scripts/release.ps1` (all four in sequence)

NPM wrappers:

- `npm run ship:base`
- `npm run ship:plus`
- `npm run ship:max`

For the exact end-to-end commands, see [SHIP_PRODUCTS_COMMANDS.md](../SHIP_PRODUCTS_COMMANDS.md).
