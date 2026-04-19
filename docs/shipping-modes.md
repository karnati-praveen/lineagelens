# Shipping Modes

LineageLens ships from one codebase in three release modes.

## Solo

- Extension only.
- No backend required.
- Uses `aiCodeProvenance.mode = local`.
- Best for single-user and offline workflows.

Artifact target:

- `lineagelens-solo-<version>.vsix`

## Team

- Extension plus backend-basic.
- Shared ingest, auth, search, and dashboard.
- No Neo4j dependency.

Artifacts:

- `lineagelens-team-<version>.vsix`
- `docker-compose.team.yml`
- `.env.team.example`

## Enterprise

- Extension plus backend-full.
- Shared ingest, auth, search, dashboard, vector search, and Neo4j lineage.

Artifacts:

- `lineagelens-enterprise-<version>.vsix`
- `docker-compose.enterprise.yml`
- `.env.enterprise.example`

## Release Helpers

PowerShell helpers:

- `scripts/package-solo.ps1`
- `scripts/package-team.ps1`
- `scripts/package-enterprise.ps1`

NPM wrappers:

- `npm run ship:solo`
- `npm run ship:team`
- `npm run ship:enterprise`

For the exact end-to-end commands, see [SHIP_PRODUCTS_COMMANDS.md](../SHIP_PRODUCTS_COMMANDS.md).
