# LineageLens Repo Map

This file is a compact guide to the repository layout so an AI can quickly understand where the important code lives and what each folder is for.

## Top-Level View

```text
LineageLens/
  README.md              # User-facing overview and operating modes
  file.md                # This repo map
  prafea                 # Long-form architecture and feature reference
  package.json           # VS Code extension manifest, commands, settings, scripts
  tsconfig.json          # TypeScript compiler settings
  docker-compose.yml     # Backend stack for local development
  backend/               # FastAPI backend application
  src/                   # VS Code extension source
  docs/                  # Supporting documentation
  media/                 # Icons and other static assets
  dist/                  # Built extension output
  out/                   # TypeScript build output
  node_modules/          # Installed Node dependencies
  .venv/                 # Python virtual environment
```

## What Each Main Folder Does

### `src/`
VS Code extension source code. This is where the editor integration, local storage, adapter detection, provenance record building, search UI, dashboard UI, and backend client live.

Important subfolders and files:

```text
src/
  extension.ts              # Extension activation and command wiring
  backend.ts                # Backend ingest/search payload helpers
  backendAuth.ts            # Backend authentication helpers
  correlation.ts            # Matches editor insertions to proxy captures
  eventSchema.ts            # Provider-agnostic provenance event schema
  lightweightRecord.ts      # VS Code-free lightweight record builder
  insights.ts               # Risk scoring and dashboard logic
  insightsDashboard.ts      # Dashboard webview UI
  reviewer.ts               # Current-file review workflow
  provenance.ts             # Provenance record types and AST helpers
  proxy.ts                  # Local proxy capture and telemetry
  storage/                  # Local and backend storage service implementations
  agentAdapters/            # Cursor, Claude Code, Aider, and fallback adapters
  test/                     # TypeScript unit tests
```

### `backend/`
FastAPI backend for ingesting, storing, searching, explaining, and aggregating provenance records.

Important subfolders and files:

```text
backend/
  app/
    main.py                 # FastAPI app startup, health, and middleware
    core/                   # Settings, security, rate limiting
    db/                     # SQLAlchemy models and session management
    schemas/                # Pydantic request/response schemas
    services/               # Ingest, search, insights, explanation, Neo4j
    api/routes/             # HTTP and WebSocket endpoints
  alembic/                  # Database migrations
  tests/                    # Backend unit tests
  requirements.txt          # Runtime Python dependencies
  requirements-dev.txt      # Development/test dependencies
  Dockerfile                # Backend container build
  pytest.ini                # Pytest configuration
```

### `docs/`
Documentation for the architecture and adapter strategy.

- `docs/lightweight-adapters.md` explains the lightweight CLI boundary and backend mode behavior.

### `media/`
Static assets for the extension, such as the icon used in the VS Code UI.

### `dist/` and `out/`
Generated build output.

- `dist/` is the packaged extension bundle.
- `out/` is the TypeScript compiler output used during development.

### `node_modules/` and `.venv/`
Generated dependency directories.

- `node_modules/` holds installed npm packages.
- `.venv/` holds the Python virtual environment for the backend.

## Key Runtime Flow

1. The VS Code extension activates from `src/extension.ts`.
2. Editor changes are correlated with proxy captures in `src/correlation.ts` and `src/proxy.ts`.
3. The extension builds a provenance record in `src/provenance.ts` and `src/eventSchema.ts`.
4. Records are stored locally through `src/storage/LocalStorageService.ts` or sent to the backend through `src/storage/BackendStorageService.ts`.
5. The backend receives ingest requests through `backend/app/api/routes/ingest.py` or `backend/app/api/routes/ws_capture.py`.
6. Search and dashboard results come from `backend/app/services/provenance_service.py` and `backend/app/services/insights_service.py`.

## How To Read The Project Fast

If you want the shortest path to understanding the system, read in this order:

1. `README.md` for the user-facing mode split.
2. `src/extension.ts` for the extension entry point.
3. `src/eventSchema.ts` and `src/lightweightRecord.ts` for the shared provenance contract.
4. `src/storage/StorageService.ts` and the files under `src/storage/` for local versus backend behavior.
5. `backend/app/main.py` and `backend/app/services/ingest_normalizer.py` for backend startup and ingest normalization.
6. `backend/app/services/provenance_service.py` and `backend/app/services/insights_service.py` for storage, search, and dashboard logic.

## Notes For AI Readers

- The repo is a VS Code extension plus a FastAPI backend.
- Local mode should work without backend dependencies.
- Backend basic mode should work without Neo4j or vector search.
- Backend full mode enables the graph/vector features.
- Claude Code, Cursor, and Aider are supported through the adapter layer, but terminal-only CLI support still needs a dedicated executable entry point if you want a standalone `.exe`.