# LineageLens

LineageLens tracks AI-assisted code insertions and links them to provenance context.

## Operating Modes

### Solo Mode (`local`, default)

Zero setup. Fully private. Works offline.

- No backend setup required.
- Data is stored locally in VS Code storage (or optional workspace file mode).
- Best for individual and offline workflows.
- Start the extension from Command Palette (`Ctrl+Shift+P`) by running `Start LineageLens`.

### Team Mode (`backend basic`)

Simple team sharing without complexity.

- Connects to your FastAPI backend for shared/team workflows.
- Enables backend ingest, auth, search, and shared provenance storage.
- Runs without Neo4j or vector search.
- Use `docker-compose.team.yml` plus `.env.team.example` when shipping it separately.

### Enterprise Mode (`backend full`)

Complete provenance intelligence and auditability.

- Enables backend ingest, auth, search, and lineage graph capabilities.
- Uses Neo4j lineage and vector search.
- Best for the complete production setup.
- Use `docker-compose.enterprise.yml` plus `.env.enterprise.example` when shipping it separately.

See [docs/lightweight-adapters.md](docs/lightweight-adapters.md) for the lightweight adapter contract.
See [docs/shipping-modes.md](docs/shipping-modes.md) for the release layout and [SHIP_PRODUCTS_COMMANDS.md](SHIP_PRODUCTS_COMMANDS.md) for exact ship commands.

## Switch from Pure Local to Backend Mode

1. Start your backend service (default: `http://127.0.0.1:8787`).
2. In VS Code, open Command Palette (`Ctrl+Shift+P`).
3. Run `AI Provenance: Switch to Backend Mode`.
4. Run `AI Insertion Detector: Backend Login` and authenticate.
5. Optional: set backend endpoints in Settings if different from defaults.

### Settings JSON Example

```json
{
  "aiCodeProvenance.mode": "backend",
  "aiInsertionDetector.backend.baseUrl": "http://127.0.0.1:8787",
  "aiInsertionDetector.backend.websocketUrl": "ws://127.0.0.1:8787/ws/capture"
}
```

## Useful Commands

- `Start LineageLens`
- `AI Insertion Detector: Toggle Feature`
- `AI Insertion Detector: Show Status`
- `AI Insertion Detector: Show Provenance`
- `AI Provenance: Show Adapter Diagnostics`
- `AI Insertion Detector: Open Provenance Search`
- `AI Insertion Detector: Backend Login`

## Native Agent Adapters

LineageLens now normalizes agent metadata for:

- Cursor
- Claude Code
- Aider

Each match stores normalized session details when available:

- `toolName`, `provider`, `sessionId`, `conversationId`, `runId`
- `modelName`, `userAgent`, `workspaceHint`, `operationType`
- `confidence` and evidence used for the match

When a native adapter cannot confidently match a record, LineageLens falls back to the legacy heuristic path so older records still work.

## Provider-Agnostic Provenance Core

The VS Code extension is now treated as a thin shim over a shared provenance event contract. Each stored record includes:

- `schemaVersion` for versioned compatibility (`lineagelens.provenance-event.v1`)
- `normalizedEvent` for IDE/provider-neutral capture, session, model, file, diff, context, and confidence fields
- `rawData` for the original detection payload plus raw proxy request/response fragments where available
- adapter-declared capabilities so integrations report what they can provide instead of assuming every tool exposes the same data

This keeps Cursor, Claude Code, Aider, Copilot-style traffic, opaque HTTPS tunnels, and future IDE agents on the same record shape. Full captures store prompt/response bodies, metadata-only and tunnel-only captures still keep routing/session evidence, and completely opaque tools can still produce file-diff provenance.

New integrations should register a small adapter with a name, order, declared capabilities, and detector/parser logic. The adapter should populate the common event fields when possible and leave unknown data in `extensions` rather than requiring core schema changes.

## Proxy Capture Status

The local proxy reports capture state as:

- `full` for allowlisted POST bodies
- `metadata_only` when only safe metadata is available
- `tunnel_only` for CONNECT tunnels
- `unavailable` when a request is outside the allowlist

Use `AI Insertion Detector: Show Status` to view the proxy capability report.

## Adapter Diagnostics

Use `AI Provenance: Show Adapter Diagnostics` to inspect:

- which adapter matched a record
- the evidence used for the match
- the stored confidence and capture status
- the fallback metadata when a record was only heuristically grouped

## Build and Package

```bash
npm install
npm run compile
npm run package:vsix
```

Mode-specific release helpers:

- `npm run ship:solo`
- `npm run ship:team`
- `npm run ship:enterprise`
