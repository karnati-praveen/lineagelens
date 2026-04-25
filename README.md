# LineageLens

LineageLens is a provenance system for AI-generated code. It tracks code inserted by any AI coding tool — Claude Code, Cursor, Aider, Copilot, or anything else that writes files — and links each insertion to its prompt, model, session, and context. VS Code is the current observation host; the provenance record and backend are tool-agnostic.

## Operating Modes

### LineageLens Base (`local`, default)

Zero setup. Fully private. Works offline.

- No backend setup required.
- Data is stored locally in VS Code storage (or optional workspace file mode).
- Best for individual and offline workflows.
- Start the extension from Command Palette (`Ctrl+Shift+P`) by running `Start LineageLens`.

### LineageLens Plus (`backend basic`)

Shared backend workflows without graph complexity.

- Connects to your FastAPI backend for shared/backend workflows.
- Enables backend ingest, auth, search, and shared provenance storage.
- Runs without Neo4j or vector search.
- Docker is optional. Use the native Python path in [docs/native-backend.md](docs/native-backend.md) if you do not want Docker Desktop.
- Use `deploy/docker-compose.plus.yml` plus `deploy/.env.plus.example` for the LineageLens Plus bundle.

### LineageLens Max (`backend full`)

Complete provenance intelligence and auditability.

- Enables backend ingest, auth, search, and lineage graph capabilities.
- Uses Neo4j lineage and vector search.
- Best for the complete production setup.
- Docker is optional. Use the native Python path in [docs/native-backend.md](docs/native-backend.md) if you want to run the backend without Docker Desktop.
- Use `deploy/docker-compose.max.yml` plus `deploy/.env.max.example` for the LineageLens Max bundle.

See [docs/lightweight-adapters.md](docs/lightweight-adapters.md) for the lightweight adapter contract.
See [docs/shipping-modes.md](docs/shipping-modes.md) for the release layout and [docs/SHIP_PRODUCTS_COMMANDS.md](docs/SHIP_PRODUCTS_COMMANDS.md) for exact ship commands.

## Switch from Pure Local to Backend Mode

1. Start your backend service (default: `http://127.0.0.1:8787`).
2. In VS Code, open Command Palette (`Ctrl+Shift+P`).
3. Run `AI Provenance: Switch to Backend Mode`.
4. Run `AI Insertion Detector: Backend Login` and authenticate.
5. Optional: set backend endpoints in Settings if different from defaults.

## Native Python Backend (No Docker Desktop)

If you want LineageLens Plus or LineageLens Max without Docker Desktop, run the backend in your local Python environment and point it at a PostgreSQL instance you already have.

- LineageLens Plus needs PostgreSQL only.
- LineageLens Max needs PostgreSQL plus Neo4j.
- Use [docs/native-backend.md](docs/native-backend.md) for the full setup and the PowerShell launch scripts.
- The native test command is `npm run native:test`.

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

The VS Code extension is a thin observation shim, not the product. The underlying provenance contract works the same regardless of which tool wrote the code. Each stored record includes:

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

- `npm run ship:base`
- `npm run ship:plus`
- `npm run ship:max`

Native backend helpers:

- `npm run native:plus`
- `npm run native:max`
- `npm run native:test`
