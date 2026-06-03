# LineageLens Max — Command Reference

Run all commands from the **root folder** (where `quickstart.sh` lives).

---

## Quick URLs

| Service          | URL                               |
|------------------|-----------------------------------|
| **Dashboard**    | http://localhost:8787/dashboard   |
| Backend API      | http://localhost:8787             |
| API Docs         | http://localhost:8787/docs        |
| Proxy            | http://localhost:8788             |
| Neo4j Browser    | http://localhost:7474             |
| Neo4j Bolt       | bolt://localhost:7687             |

---

## Option A — CLI (Recommended)

Install the `lineagelens` npm package once and manage everything with simple commands.

```bash
# Install globally (requires Node.js 18+)
npm install -g lineagelens-cli

# Start Max backend (Docker must be running — Neo4j takes ~60s on first boot)
lineagelens start --mode max

# Check status of all containers
lineagelens status

# Tail all logs
lineagelens logs --mode max

# Tail a specific service
lineagelens logs --mode max --service backend
lineagelens logs --mode max --service proxy
lineagelens logs --mode max --service postgres
lineagelens logs --mode max --service neo4j

# Stop (data is preserved)
lineagelens stop --mode max

# Stop and delete all data including Neo4j graph (irreversible)
lineagelens stop --mode max --volumes
```

After `lineagelens start`, open **http://localhost:8787/dashboard** in your browser.

---

## Option B — Docker Compose from Bundle

Use if you prefer direct control or have customized the compose file.

### First-Time Setup

```bash
# One command — generates secrets, starts all services, waits for Neo4j
bash quickstart.sh
```

> Neo4j takes 60–90 seconds on first boot. quickstart.sh waits automatically.

### Convenience variables

```bash
COMPOSE_FILE=lineagelens-deploy/docker-compose.max.yml
ENV_FILE=lineagelens-deploy/.env
PROJECT=lineagelens-max
```

### Start / Stop / Restart

```bash
# Start all services
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  up --detach

# Stop (keeps containers and data)
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  stop

# Remove containers (volumes survive)
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  down

# Restart one service
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  restart backend

# Restart Neo4j only
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  restart neo4j

# Pull latest images and recreate
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  up --detach --pull always
```

### Logs

```bash
# All services
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  logs --follow

# Backend, last 100 lines
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  logs --follow --tail 100 backend

# Neo4j startup logs (watch for "Started.")
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  logs --follow neo4j

# Proxy
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  logs --follow proxy

# PostgreSQL
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  logs --follow postgres
```

---

## Dashboard

The web dashboard is the primary UI — no VS Code required.

```
http://localhost:8787/dashboard
```

**First login:**
1. Open http://localhost:8787/dashboard in your browser
2. Click **Register** — enter workspace ID, username, password
3. You become the admin of your workspace automatically

**What the dashboard includes (Max):**
- Governance overview — records, risk score, compliance controls, agent sessions
- Provenance search — keyword, model, date, file path (set EMBEDDING_PROVIDER=openai for semantic similarity)
- Record viewer — full prompt, inserted code, context snapshot, AI explanation, evolution chain
- Graph lineage — powered by Neo4j (query via Neo4j Browser at http://localhost:7474)
- Audit export (admin) — CSV with date / developer / file filters, up to 10,000 rows
- Team management (admin) — invite members, view per-user AI activity

---

## Find Your Credentials

Forgot your username, workspace, password, or Neo4j password? Run these from inside the unzipped folder.

```bash
# ── Workspace ID and Username ──────────────────────────────────────────
# Shows all registered users, their workspace, and role
docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance \
  -c "SELECT username, workspace_id, role, created_at FROM users ORDER BY created_at;"

# ── Postgres password (generated by quickstart.sh) ────────────────────
cat lineagelens-deploy/.env | grep POSTGRES_PASSWORD

# ── Neo4j password ────────────────────────────────────────────────────
cat lineagelens-deploy/.env | grep NEO4J

# ── JWT secrets ───────────────────────────────────────────────────────
cat lineagelens-deploy/.env | grep JWT

# ── All .env values at once ───────────────────────────────────────────
cat lineagelens-deploy/.env

# ── Full .env path ────────────────────────────────────────────────────
echo "Config file: $(pwd)/lineagelens-deploy/.env"
```

> **Forgot your password?** Passwords are hashed — you cannot recover them.
> Reset by running `bash reset.sh`, then re-register via the dashboard.

---

## Authentication

**First admin:** visit `http://localhost:8787` in your browser — you'll be redirected to the
setup wizard at `/setup`. Fill in your admin username, password, and workspace name. No curl needed.

**Add teammates:** log in as admin → Team tab → Generate Invite Link → copy the link and send
it to the engineer. They open it in a browser, pick a username and password, and land in the
dashboard. No curl needed.

### API / scripting (after setup)

```bash
# Login and save token
# Note: response key is accessToken (camelCase)
TOKEN=$(curl -s -X POST http://localhost:8787/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"yourpassword"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('accessToken') or d.get('access_token','LOGIN_FAILED'))")

echo "Token: ${TOKEN:0:20}..."

# If TOKEN prints LOGIN_FAILED — check username/password above, then re-login
# See the full login response to debug:
curl -s -X POST http://localhost:8787/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"yourpassword"}' \
  | python3 -m json.tool

echo "Token saved: ${TOKEN:0:20}..."

# View profile
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8787/auth/me | python3 -m json.tool

# Logout
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8787/auth/logout
```

---

## Proxy — Capture AI Traffic

```bash
# Proxy health
curl -s http://localhost:8788/health

# ── Claude Code (uses its own config — export does NOT work) ──────────
claude config set apiBaseUrl http://localhost:8788
# Verify:
claude config get apiBaseUrl
# Restore when done capturing:
# claude config set apiBaseUrl https://api.anthropic.com

# ── Other AI tools (Aider, Continue, Cursor, etc.) ───────────────────
export OPENAI_BASE_URL=http://localhost:8788
export ANTHROPIC_BASE_URL=http://localhost:8788

# Set ingest token so the proxy can write to the backend
# 1. Login and get JWT (see Authentication above)
# 2. Edit lineagelens-deploy/.env:
#      PROXY_INGEST_TOKEN=<your JWT>
# 3. Restart proxy:
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  restart proxy

# Verify proxy is forwarding
docker logs lineagelens-max-proxy --tail 20
```

---

## Neo4j — Graph Lineage

Neo4j stores the full lineage graph: which code was inserted, evolved from what, in which session.

```bash
# Open Neo4j Browser (visual graph explorer)
open http://localhost:7474          # macOS
xdg-open http://localhost:7474      # Linux
start http://localhost:7474         # Windows
# Login: username=neo4j, password=<NEO4J_PASSWORD from lineagelens-deploy/.env>

# Neo4j health check
curl -s http://localhost:7474

# Cypher shell — run graph queries
docker exec -it lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)"

# Count all lineage nodes
docker exec lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)" \
  "MATCH (n) RETURN labels(n) AS type, count(n) AS total ORDER BY total DESC"

# Find all insertions for a file
docker exec lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)" \
  "MATCH (n {filePath:'src/auth.py'}) RETURN n LIMIT 25"

# View evolution chain of a record
docker exec lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)" \
  "MATCH path=(n {uuid:'<uuid>'})-[:EVOLVED_FROM*]->(root) RETURN path"

# Neo4j memory usage
docker exec lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)" \
  "CALL dbms.listConfig() YIELD name, value WHERE name CONTAINS 'memory' RETURN name, value"
```

---

## Team Management

**Invite a teammate:** admin dashboard → Team tab → Generate Invite Link. Set role, expiry,
and max-uses, then copy the link and send it. The recipient opens it in a browser and signs
up without needing any credentials from you.

```bash
# List all team members (API / scripting)
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8787/team/members | python3 -m json.tool
```

---

## Provenance API

```bash
# Search records (keyword; set EMBEDDING_PROVIDER=openai for semantic similarity)
curl -s -X POST http://localhost:8787/search \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"workspace_id":"your-workspace","keywords":"authentication"}' \
  | python3 -m json.tool

# Search by model and date
curl -s -X POST http://localhost:8787/search \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"workspace_id":"your-workspace","model":"gpt-4o","date_from":"2024-01-01T00:00:00Z"}' \
  | python3 -m json.tool

# Get record by UUID
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8787/provenance/<uuid> | python3 -m json.tool

# Governance dashboard (full analytics)
curl -s -X POST http://localhost:8787/insights/dashboard \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"workspace_id":"your-workspace"}' | python3 -m json.tool

# Export audit CSV (admin, up to 10,000 rows)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8787/export/audit?dateFrom=2024-01-01T00:00:00Z" \
  -o lineagelens-audit.csv && echo "Saved: lineagelens-audit.csv"

# Export filtered by developer
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8787/export/audit?developer=alice" \
  -o lineagelens-audit-alice.csv

# AI explain a record
curl -s -X POST http://localhost:8787/explain \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"uuid":"<uuid>","workspace_id":"your-workspace"}' \
  | python3 -m json.tool
```

---

## Health & Diagnostics

```bash
# Backend health (no auth)
curl -s http://localhost:8787/health | python3 -m json.tool
# Expect: "productMode": "max", "neo4j": true

# Proxy health
curl -s http://localhost:8788/health

# Neo4j health
curl -s http://localhost:7474

# Run automated debug script
bash debug.sh

# Container status
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env ps

# Resource usage (Neo4j is memory-heavy — needs 1-2 GB free)
docker stats --no-stream

# Database migration status
docker exec lineagelens-max-backend \
  sh -c 'cd /app && alembic current'

# Run pending migrations
docker exec lineagelens-max-backend \
  sh -c 'cd /app && alembic upgrade head'

# PostgreSQL shell
docker exec -it lineagelens-max-postgres \
  psql -U postgres -d provenance

# Count captured records
docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance \
  -c "SELECT COUNT(*) FROM provenance_records;"

# Count Neo4j lineage nodes
docker exec lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)" \
  "MATCH (n) RETURN count(n)"
```

---

## Update to Latest Version (Zero-Downtime)

```bash
# 1. Pull latest images without stopping
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env pull

# 2. Recreate containers (Neo4j is recreated last — graph data volume persists)
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  up --detach --force-recreate

# 3. Run any pending database migrations (idempotent — safe to re-run)
docker exec lineagelens-max-backend \
  sh -c 'cd /app && alembic upgrade head'

# 4. Verify backend + Neo4j
curl -s http://localhost:8787/health | python3 -m json.tool
# Expect: "productMode": "max", "neo4j": true, "status": "ok"
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:7474
# Expect: 200
```

### Upgrade checklist

- [ ] Check release notes for breaking changes before upgrading
- [ ] Back up PostgreSQL before any migration that adds/removes columns (see Backup section below)
- [ ] Back up Neo4j volume before major upgrades (see Neo4j Backup section below)
- [ ] Verify `alembic upgrade head` exits with code 0
- [ ] Confirm `/health` returns the new `version` value and `"neo4j": true`

Or with the CLI:

```bash
lineagelens stop --mode max
lineagelens start --mode max   # pulls latest by default
```

---

## PostgreSQL Backup

### One-shot dump

```bash
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
docker exec lineagelens-max-postgres \
  pg_dump -U postgres -d provenance --format=custom \
  > lineagelens-provenance-max-${TIMESTAMP}.dump
```

### Restore from dump

```bash
# Stop backend first so no writes happen during restore
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env stop backend

docker exec -i lineagelens-max-postgres \
  pg_restore -U postgres -d provenance --clean --if-exists \
  < lineagelens-provenance-max-20240501-120000.dump

# Restart backend
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env start backend
```

### Scheduled backup (cron example)

```cron
# Daily at 02:00 — keep last 7 dumps
0 2 * * * docker exec lineagelens-max-postgres pg_dump -U postgres -d provenance --format=custom > /backups/provenance-$(date +\%Y\%m\%d).dump && find /backups -name "provenance-*.dump" -mtime +7 -delete
```

---

## Neo4j Backup

### Snapshot via volume tar

```bash
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Stop Neo4j so it flushes to disk cleanly
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env stop neo4j

docker run --rm \
  --volumes-from lineagelens-max-neo4j \
  -v "$(pwd)/backups:/backup" \
  alpine \
  tar czf /backup/neo4j-data-${TIMESTAMP}.tar.gz /data

docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env start neo4j
```

### Restore from backup

```bash
# Stop the container
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env stop neo4j

# Remove existing volume and restore from archive
docker volume rm lineagelens-max_neo4j_data 2>/dev/null || true
docker run --rm \
  --volumes-from lineagelens-max-neo4j \
  -v "$(pwd)/backups:/backup" \
  alpine \
  sh -c "cd / && tar xzf /backup/neo4j-data-20240501-020000.tar.gz"

docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env start neo4j
```

---

## Neo4j Partial-Failure Reconciliation

When Neo4j is temporarily unreachable at ingest time, records are stored in PostgreSQL without a graph node — `lineage_node_id` is NULL. The backend logs: `Neo4j lineage is unavailable; record stored without graph lineage.`

### Identify orphaned records

```bash
# Count records with no lineage node
docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance \
  -c "SELECT COUNT(*) FROM provenance_records WHERE lineage_node_id IS NULL;"

# List the most recent 20 orphaned records
docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance \
  -c "SELECT uuid, workspace_id, file_path, timestamp_iso FROM provenance_records WHERE lineage_node_id IS NULL ORDER BY timestamp_iso DESC LIMIT 20;"
```

### Re-ingest orphaned records

Orphaned records are fully queryable in PostgreSQL and the dashboard. To backfill lineage nodes after Neo4j recovers:

1. Confirm Neo4j is healthy: `curl -s http://localhost:7474`
2. Re-POST each orphaned record to `/ingest`. UUID deduplication skips the DB insert; Neo4j write is retried.

```bash
TOKEN=<your-jwt>

UUIDS=$(docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance -t \
  -c "SELECT uuid FROM provenance_records WHERE lineage_node_id IS NULL LIMIT 100;" \
  | tr -d ' ')

for UUID in $UUIDS; do
  RECORD=$(curl -s -H "Authorization: Bearer $TOKEN" \
    http://localhost:8787/provenance/$UUID)

  PAYLOAD=$(echo "$RECORD" | python3 -c "import sys,json; r=json.load(sys.stdin); print(json.dumps(r.get('record', {})))")

  curl -s -X POST http://localhost:8787/ingest \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $TOKEN" \
    -d "$PAYLOAD" | python3 -m json.tool
done
```

### Prevention

Set `LINEAGE_STRICT_MODE=true` in `lineagelens-deploy/.env` to make the backend refuse to start if Neo4j is unreachable. This prevents any records being written without a corresponding lineage node, at the cost of hard failing instead of degrading gracefully.

---

## Rate Limiter: Single-Replica Note

The default rate limiter (`InMemoryRateLimiter`) is **process-local**. In a single-container deployment (the standard case) it works correctly. If you ever scale to multiple backend replicas behind a load balancer, each replica tracks its own counter — a single client can exceed the limit by hitting different replicas.

### Fix: enable Redis-backed rate limiting

Set `REDIS_URL` in `lineagelens-deploy/.env`:

```bash
REDIS_URL=redis://redis:6379/0
```

Add a Redis service to your compose file, then restart the backend. The backend auto-detects `REDIS_URL` at startup and switches to the shared Redis limiter.

---

## Environment Variable Reference

### Proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `UPSTREAM_URL` | `https://api.anthropic.com` | Upstream LLM API to proxy to |
| `BACKEND_URL` | `http://backend:8787` | LineageLens backend URL |
| `BACKEND_INGEST_TOKEN` | _(required)_ | JWT the proxy uses to POST to `/ingest` |
| `PROXY_WORKSPACE_ID` | `proxy-capture` | Workspace ID for proxy-captured records |
| `PROXY_PORT` | `8788` | Port the HTTP proxy listens on |
| `PROXY_REDACT_PATTERNS` | _(empty)_ | Comma-separated regex patterns redacted from captured content. Example: `Bearer [A-Za-z0-9._-]+,sk-[A-Za-z0-9]+` |
| `PROXY_CONNECT_PORT` | `8789` | Port for the HTTPS CONNECT tunnel server |
| `PROXY_CA_CERT_PATH` | _(empty)_ | CA cert PEM for HTTPS CONNECT MITM. If unset, falls back to transparent TCP relay. |
| `PROXY_CA_KEY_PATH` | _(empty)_ | CA private key PEM (paired with `PROXY_CA_CERT_PATH`) |

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | _(empty)_ | Redis URL. When set, enables shared rate limiting across replicas. |
| `LINEAGE_STRICT_MODE` | `false` | If `true`, backend refuses to start when Neo4j is unreachable |
| `RATE_LIMIT_ENABLED` | `true` | Enable or disable the HTTP rate limiter |
| `RATE_LIMIT_MAX_REQUESTS` | `120` | Requests per window per client IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit sliding window in seconds |

---

## Reset (Wipe All Data)

```bash
# Automated reset (stops containers, removes volumes and .env)
bash reset.sh

# Manual reset
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  down --volumes --remove-orphans
rm -f lineagelens-deploy/.env
echo "Done. Run: bash quickstart.sh"
```

---

## Troubleshooting

### Dashboard blank or "Failed to load"

```bash
# Check backend is up
curl -s http://localhost:8787/health

# Container overview
docker ps | grep lineagelens-max

# Backend crash logs
docker logs lineagelens-max-backend --tail 50
```

### Neo4j takes too long to start / backend won't connect to Neo4j

```bash
# Neo4j needs 60–90s on first boot. Watch logs:
docker logs -f lineagelens-max-neo4j | grep -E "Started|ERROR|WARN"
# Wait until you see "Remote interface available at http://localhost:7474/"

# Check Neo4j health manually
curl -s http://localhost:7474

# If Neo4j fails to authenticate (password mismatch after a reset):
# 1. Remove the Neo4j volume
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  down --volumes
# 2. Re-run quickstart to regenerate secrets and volumes
bash quickstart.sh
```

### Backend can't connect to Neo4j ("Neo4j is unavailable")

```bash
# Check NEO4J_URI and NEO4J_PASSWORD in .env
grep NEO4J lineagelens-deploy/.env

# Verify Neo4j bolt port is accepting connections
docker exec lineagelens-max-neo4j \
  cypher-shell -a bolt://localhost:7687 -u neo4j \
  -p "$(grep NEO4J_PASSWORD lineagelens-deploy/.env | cut -d= -f2)" "RETURN 1"

# View backend Neo4j connection logs
docker logs lineagelens-max-backend 2>&1 | grep -i neo4j
```

### Login returns 401 / wrong credentials

```bash
docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance -c "SELECT username, role FROM users;"
```

### Port conflict (8787, 8788, 7474, 7687)

```bash
lsof -i :8787
lsof -i :7474
lsof -i :7687
kill -9 <PID>
```

### Backend container keeps restarting

```bash
docker logs lineagelens-max-backend --tail 50

# Fix missing JWT secrets
openssl rand -hex 32   # JWT_SECRET_KEY
openssl rand -hex 32   # JWT_REFRESH_SECRET_KEY
# Add to lineagelens-deploy/.env, then:
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env restart backend
```

### Migrations fail

```bash
# Ensure postgres is healthy
docker exec lineagelens-max-postgres pg_isready -U postgres -d provenance

# Run migrations manually
docker exec lineagelens-max-backend sh -c 'cd /app && alembic upgrade head'
```

### Proxy not capturing

```bash
curl -s http://localhost:8788/health
grep PROXY_INGEST_TOKEN lineagelens-deploy/.env
docker logs lineagelens-max-proxy --tail 30
```

### "This endpoint requires backend mode: enterprise"

```bash
docker exec lineagelens-max-backend env | grep BACKEND_MODE
# Should print: BACKEND_MODE=enterprise
```

### Search returns no results

```bash
docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance -c "SELECT COUNT(*) FROM provenance_records;"
# If 0: proxy isn't capturing yet — confirm PROXY_INGEST_TOKEN and AI tool URL
```

### Neo4j out of memory

Neo4j requires at least 2 GB of free RAM. If the container keeps crashing:

```bash
# Check memory config in compose file:
# NEO4J_server_memory_heap_max__size: 1024M
# Reduce to 512M if on a low-memory machine

docker stats lineagelens-max-neo4j --no-stream
```

### Out of disk space

```bash
docker system df
docker image prune -f
docker system prune -f
```

---

## MCP Server

The LineageLens MCP server lets Claude Code, Cursor, Continue, and any other MCP-capable client query provenance records — including Neo4j-backed graph lineage data — directly inside the AI chat.

> **Multi-user warning:** Each user must run their **own** MCP server process with their **own** credentials. The MCP server holds a single JWT in memory for the lifetime of the process. If you share one server instance across multiple users (e.g., by pointing multiple AI tools at the same running process), every user will query under the first user's token and workspace — leaking provenance data across accounts. One process = one user.

### Prerequisites

- Python 3.11 or later
- The Max backend running (`lineagelens start --mode max` or `bash quickstart.sh`)
- A registered LineageLens account on this backend

### Install dependencies

```bash
cd lineagelens-mcp
pip install -r lineagelens-mcp-requirements.txt
```

With a virtual environment (recommended):

```bash
cd lineagelens-mcp
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.\.venv\Scripts\activate       # Windows
pip install -r lineagelens-mcp-requirements.txt
```

### Get your access token (one-time)

```bash
# Login and capture your JWT
TOKEN=$(curl -s -X POST http://localhost:8787/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"yourpassword"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('accessToken',''))")
echo $TOKEN
```

### Test the server runs

```bash
cd lineagelens-mcp
LINEAGELENS_USERNAME=admin \
LINEAGELENS_PASSWORD=yourpassword \
LINEAGELENS_BACKEND_URL=http://localhost:8787 \
python lineagelens-mcp.py
# No error = ready. Ctrl+C to stop.
```

### Wire into Claude Code

Add to `~/.claude/settings.json` (global) or `.claude/settings.json` (project):

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "python",
      "args": ["/absolute/path/to/lineagelens-mcp/lineagelens-mcp.py"],
      "env": {
        "LINEAGELENS_USERNAME": "admin",
        "LINEAGELENS_PASSWORD": "yourpassword",
        "LINEAGELENS_BACKEND_URL": "http://localhost:8787"
      }
    }
  }
}
```

Replace `/absolute/path/to/lineagelens-mcp/lineagelens-mcp.py` with the full path on your machine. Restart Claude Code after saving.

Alternatively, use a pre-obtained token instead of username/password:

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "python",
      "args": ["/absolute/path/to/lineagelens-mcp/lineagelens-mcp.py"],
      "env": {
        "LINEAGELENS_ACCESS_TOKEN": "<paste token here>",
        "LINEAGELENS_BACKEND_URL": "http://localhost:8787"
      }
    }
  }
}
```

### Wire into Cursor

Cursor → Settings → MCP → Add Server:

- **Name:** `lineagelens`
- **Command:** `python /absolute/path/to/lineagelens-mcp/lineagelens-mcp.py`
- **Environment variables:**
  - `LINEAGELENS_USERNAME` = admin
  - `LINEAGELENS_PASSWORD` = yourpassword
  - `LINEAGELENS_BACKEND_URL` = http://localhost:8787

### Available MCP tools

Once connected, the AI assistant can call:

| Tool | What it does |
|------|-------------|
| `search_provenance(query)` | Full-text keyword search for AI-generated code (semantic similarity requires EMBEDDING_PROVIDER=openai) |
| `get_record(uuid)` | Full metadata for a specific provenance record (includes Neo4j lineage node ID) |
| `get_insights()` | Governance dashboard — risk scores, compliance, totals, agent sessions |
| `explain_record(uuid)` | Plain-English explanation of why the code was generated |
| `list_recent(limit)` | Most recently captured AI insertions |
| `check_file_risk(file_path)` | Risk breakdown and model usage for a specific file |

> **Max note:** `get_record` returns the `lineageNodeId` field when the record has a Neo4j lineage node. Use this value in Cypher queries (see Neo4j section above) to traverse the full evolution graph.

### Windows (PowerShell) environment setup

```powershell
$env:LINEAGELENS_USERNAME = "admin"
$env:LINEAGELENS_PASSWORD = "yourpassword"
$env:LINEAGELENS_BACKEND_URL = "http://localhost:8787"
cd lineagelens-mcp
python server.py
```

### Verify the MCP server can reach the backend

```bash
curl -s http://localhost:8787/health | python3 -m json.tool
# Should show "productMode": "max", "neo4j": true
```

### Troubleshooting MCP

**"Authentication required" on startup**
- `LINEAGELENS_USERNAME` and `LINEAGELENS_PASSWORD` (or `LINEAGELENS_ACCESS_TOKEN`) must be set before starting the server.

**"Backend returned 401"**
- Token expired. Restart the MCP server process — it will re-login automatically.
- Or re-obtain the token and update `LINEAGELENS_ACCESS_TOKEN`.

**"Backend returned 403"**
- The user account does not have access to this workspace.
- Check the workspace ID matches: `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8787/auth/me`

**"Connection refused" / "Backend returned 503"**
- The Max backend is not running. Start it: `lineagelens start --mode max`
- Neo4j may still be booting — wait 60–90 s, then retry.

**`get_record` returns no `lineageNodeId`**
- The record was ingested before Neo4j was enabled, or Neo4j was unreachable at ingest time.
- Re-ingest or check Neo4j connectivity: `curl -s http://localhost:7474`

---

## Quick Diagnostics Checklist

```bash
echo "1. Containers running?"
docker ps | grep lineagelens-max

echo "2. Backend healthy?"
curl -s http://localhost:8787/health | python3 -m json.tool

echo "3. Neo4j reachable?"
curl -s -o /dev/null -w "Neo4j HTTP: %{http_code}\n" http://localhost:7474

echo "4. Dashboard reachable?"
curl -s -o /dev/null -w "Dashboard: %{http_code}\n" http://localhost:8787/dashboard

echo "5. Recent backend errors?"
docker logs lineagelens-max-backend --tail 20

echo "6. Run full automated debug"
bash debug.sh
```
