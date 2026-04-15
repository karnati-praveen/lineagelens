# AI Insertion Detector + Provenance Backend

End-to-end system for AI code provenance:
- VS Code extension detects qualifying insertions.
- Local proxy captures LLM request/response pairs.
- Correlation links insertions to prompts/responses.
- FastAPI backend stores provenance + embeddings + AST + lineage.
- Neo4j stores lineage graph versions/edges.
- Sidebar/search UI surfaces provenance in-editor.
- GitHub Action comments provenance context on PRs.

## What You Get

- Insertion detection with configurable line threshold.
- Prompt correlation by timing + optional content similarity.
- Real-time ingest over WebSocket with HTTP fallback.
- Per-user JWT auth with workspace-scoped isolation.
- Provenance search and explanation endpoints.
- Lineage graph version tracking in Neo4j.
- Configurable rate limiting for HTTP + WebSocket traffic.

## Operating Modes

The extension now supports two runtime modes controlled by `aiCodeProvenance.mode`.

### Pure Local Mode (`local`, default)

- Best for individual developers, privacy-focused workflows, and quick offline trials.
- Stores provenance records locally in `.vscode/ai-provenance/records.json` (or VS Code global state when no workspace folder exists).
- Captures insertions, proxy correlation metadata, context snapshots, AST normalization, and deterministic local embeddings without backend setup.
- Uses local keyword/date/model/file filtering in the search sidebar.
- Maintains a simple local evolution chain and diffs; refresh with command: `AI Provenance: Refresh Local Lineage (Latest Commit)`.
- Explanation uses templated local summaries by default, with optional local Ollama integration.

### Backend Mode (`backend`)

- Best for teams and full platform capabilities.
- Uses FastAPI WebSocket/HTTP ingest, backend auth, Neo4j lineage graph, pgvector search, team sharing, and PR review workflows.
- Switch from VS Code command palette: `AI Provenance: Switch to Backend Mode`.
- Team-only features require backend mode.

## Repository Layout

```text
.
├─ src/                              # VS Code extension source
├─ backend/
│  ├─ app/                           # FastAPI app
│  ├─ requirements.txt
│  └─ Dockerfile
├─ .github/
│  ├─ workflows/provenance-review.yml
│  └─ scripts/provenance_pr_review.py
├─ docker-compose.yml                # Sample backend stack (Postgres + Neo4j + API)
├─ package.json                      # Extension manifest/settings
└─ README.md
```

## Prerequisites

- Node.js 18+ and npm
- Python 3.11+ (for local backend run)
- Docker Desktop (recommended backend stack)
- VS Code 1.90+

## Quick Start (Recommended: Docker Compose)

1. Build extension dependencies:

```bash
npm install
npm run compile
```

2. Start backend stack:

```bash
docker compose up -d --build
```

3. Verify backend health:

```bash
curl http://127.0.0.1:8787/health
```

4. Launch extension in Extension Development Host:
- Open this repo in VS Code.
- Press F5.
- In the Extension Development Host, run command: AI Insertion Detector: Backend Login.

## Build & Install the VS Code Extension (.vsix)

1. Compile extension:

```bash
npm run compile
```

2. Package extension:

```bash
npx @vscode/vsce package --out ai-insertion-detector.vsix
```

3. Install .vsix:

Option A (CLI):

```bash
code --install-extension ai-insertion-detector.vsix
```

Option B (UI):
- VS Code -> Extensions view -> ... menu -> Install from VSIX...

4. Confirm commands are available:
- AI Insertion Detector: Toggle Feature
- AI Insertion Detector: Show Status
- AI Insertion Detector: Show Provenance
- AI Insertion Detector: Open Provenance Search
- AI Insertion Detector: Backend Login

## Run the FastAPI Backend

### Option A: Docker Compose (recommended)

Use the included [docker-compose.yml](docker-compose.yml).

```bash
docker compose up -d --build
```

Stop stack:

```bash
docker compose down
```

Stop and remove volumes:

```bash
docker compose down -v
```

### Option B: Local Python process

1. Create backend env file:

```bash
cd backend
cp .env.example .env
```

2. Install deps:

```bash
pip install -r requirements.txt
```

3. Run API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8787
```

4. Ensure Postgres and Neo4j are running and reachable from backend env settings.

## Sample docker-compose.yml (Backend Stack)

A working sample is included at [docker-compose.yml](docker-compose.yml).

```yaml
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: provenance-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: provenance
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  neo4j:
    image: neo4j:5
    container_name: provenance-neo4j
    restart: unless-stopped
    environment:
      NEO4J_AUTH: neo4j/neo4j_password
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: provenance-backend
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      neo4j:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/provenance
      NEO4J_URI: bolt://neo4j:7687
      JWT_SECRET_KEY: change-me
      JWT_REFRESH_SECRET_KEY: change-me-refresh
      RATE_LIMIT_ENABLED: "true"
    ports:
      - "8787:8787"

volumes:
  postgres_data:
  neo4j_data:
  neo4j_logs:
```

## Required Backend Environment Variables

Use [backend/.env.example](backend/.env.example) as the source of truth.

### App
- APP_ENV
- APP_TITLE
- APP_VERSION

### Database
- DATABASE_URL
- PGVECTOR_DIMENSION

### Neo4j
- NEO4J_URI
- NEO4J_USERNAME
- NEO4J_PASSWORD
- NEO4J_DATABASE

### JWT/Auth
- JWT_SECRET_KEY
- JWT_REFRESH_SECRET_KEY
- JWT_ALGORITHM
- JWT_AUDIENCE
- JWT_ISSUER
- JWT_ACCESS_TOKEN_TTL_MINUTES
- JWT_REFRESH_TOKEN_TTL_MINUTES
- JWT_REQUIRED_SCOPES
- AUTH_PASSWORD_MIN_LENGTH

### CORS
- BACKEND_CORS_ORIGINS

### Rate Limiting
- RATE_LIMIT_ENABLED
- RATE_LIMIT_WINDOW_SECONDS
- RATE_LIMIT_MAX_REQUESTS
- RATE_LIMIT_WS_WINDOW_SECONDS
- RATE_LIMIT_WS_MAX_MESSAGES
- RATE_LIMIT_WS_MAX_CONNECTIONS
- RATE_LIMIT_MAX_TRACKED_KEYS

### Explain Endpoint LLM
- EXPLAIN_LLM_API_URL
- EXPLAIN_LLM_API_KEY
- EXPLAIN_LLM_MODEL
- EXPLAIN_LLM_TIMEOUT_SECONDS

## Required Extension Configuration Settings

Configured under aiInsertionDetector.* in VS Code settings.

### Core Detection
- aiInsertionDetector.enabled
- aiInsertionDetector.lineThreshold
- aiInsertionDetector.correlation.similarityThreshold

### Mode Selection
- aiCodeProvenance.mode

### Local Proxy
- aiInsertionDetector.localProxy.enabled
- aiInsertionDetector.localProxy.port
- aiInsertionDetector.proxyPort

### Backend Connectivity
- aiInsertionDetector.backend.baseUrl
- aiInsertionDetector.backend.websocketUrl
- aiInsertionDetector.backend.ingestPath
- aiInsertionDetector.backend.vectorSearchPath

### Backend Auth
- aiInsertionDetector.backend.auth.loginPath
- aiInsertionDetector.backend.auth.registerPath
- aiInsertionDetector.backend.auth.refreshPath
- aiInsertionDetector.backend.auth.refreshSkewSeconds
- aiInsertionDetector.backend.auth.autoAcquireOnActivate

### Backend Retry
- aiInsertionDetector.backend.retry.websocketAttempts
- aiInsertionDetector.backend.retry.httpAttempts

### Local Explanation (Optional)
- aiInsertionDetector.local.explanation.provider
- aiInsertionDetector.local.ollama.url
- aiInsertionDetector.local.ollama.model
- aiInsertionDetector.local.ollama.timeoutMs

## Rate Limiting (Added)

Backend now enforces configurable in-memory limits:
- HTTP requests: enforced via middleware, returns 429 with Retry-After.
- WebSocket connections: limited per client.
- WebSocket ingest messages: limited per authenticated user/workspace stream.

Notes:
- Limiter state is process-local.
- For multi-instance production deployment, move rate limiting to shared storage (e.g., Redis).

## Authentication & Workspace Isolation

Backend auth endpoints:
- POST /auth/register
- POST /auth/login
- POST /auth/refresh
- GET /auth/me

Protected endpoints:
- POST /ingest
- GET /provenance/{uuid}
- POST /search
- POST /explain
- WS /ws/capture

Isolation behavior:
- JWT includes workspace_id claim.
- Read/write access is restricted to that workspace scope.
- Workspace mismatch returns 403.

## Usage Guide

### 1) Authenticate extension

In VS Code command palette:
- AI Insertion Detector: Backend Login

You can:
- Register new user
- Login existing user
- Paste existing access/refresh tokens

Tokens are stored in VS Code Secret Storage.

### 2) Trigger insertion detection and ingest

1. Ensure your coding assistant traffic is routed through local proxy (default 127.0.0.1:8080).
2. Generate and insert a qualifying block (>= line threshold).
3. Extension correlates insertion with proxy capture.
4. Extension ingests to backend via WebSocket (/ws/capture), then falls back to HTTP (/ingest) if needed.

### 3) Inspect provenance

- Use AI Insertion Detector: Show Provenance with UUID.
- Use AI Insertion Detector: Open Provenance Search to query by keyword/model/date/current file.

### 4) Review provenance in PRs

Workflow: [.github/workflows/provenance-review.yml](.github/workflows/provenance-review.yml)

Required repository secrets:
- PROVENANCE_BACKEND_API_BASE_URL
- PROVENANCE_BACKEND_API_JWT
- Optional: PROVENANCE_WORKSPACE_ID

## GitHub Action Setup

1. Add required secrets in repository settings.
2. Open or update a pull request.
3. Verify workflow run Provenance PR Review.
4. Confirm PR gets an upserted provenance summary comment.

## Troubleshooting

### Extension cannot authenticate

- Check backend is running and auth paths are correct.
- Verify JWT_* env values are consistent.
- Re-run AI Insertion Detector: Backend Login.

### 401/403 from backend

- Token expired: extension should refresh automatically, otherwise re-login.
- Workspace mismatch: ensure user/token workspace matches record workspace.

### 429 Too Many Requests

- Increase RATE_LIMIT_* values.
- Reduce request/message burst frequency.
- Check response Retry-After header.

### Proxy not intercepting

- Ensure local proxy enabled and port matches your client settings.
- Only matching POST requests to supported LLM hosts are captured.
- CONNECT-only client modes are not captured by this proxy.

### Neo4j connection failures

- Validate NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD.
- Confirm bolt port 7687 reachable.

## Developer Commands

From repository root:

```bash
npm run compile
```

From backend folder:

```bash
uvicorn app.main:app --reload --port 8787
```

Docker stack:

```bash
docker compose up -d --build
docker compose logs -f backend
```
