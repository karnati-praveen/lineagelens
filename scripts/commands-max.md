# LineageLens Max â€” Command Reference

Run all commands from the **root folder** (where `quickstart.sh` lives).

---

## Quick Variables

Copy these into your shell once to shorten every command below:

```bash
PROJECT=lineagelens-max
FILE=deploy/docker-compose.max.yml
ENV=deploy/.env
API=http://localhost:8787
PROXY=http://localhost:8788
NEO4J=http://localhost:7474
```

---

## First-Time Setup

```bash
# Full automated setup â€” runs everything in order
bash quickstart.sh
```

This single command will:
1. Check prerequisites (Docker, openssl, curl, python3)
2. Stop any leftover containers from a previous run
3. Generate random secrets and write `deploy/.env`
4. Start PostgreSQL and Neo4j
5. Wait for PostgreSQL to accept connections
6. Wait for Neo4j to become healthy (first boot takes ~60s)
7. Build the backend and proxy Docker images from source
8. Run all Alembic database migrations
9. Start the backend and proxy
10. Verify the backend health endpoint

---

## Step-by-Step Setup (manual)

Use this if `quickstart.sh` failed partway through and you need to resume.

### 1 â€” Start PostgreSQL and Neo4j

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  up -d postgres neo4j
```

### 2 â€” Wait until PostgreSQL is ready

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  exec postgres pg_isready -U postgres -d provenance
```

Re-run until you see: `provenance:5432 - accepting connections`

### 3 â€” Wait until Neo4j is healthy (first boot ~60s)

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  ps neo4j
```

Wait until the `Health` column shows `healthy`. Neo4j initialises its store on first boot â€” this can take 60â€“90 seconds on slow machines.

### 4 â€” Build backend and proxy images

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  build backend proxy
```

This must run before migrations. Skipping it may cause alembic to use a stale cached image and leave the database empty.

### 5 â€” Run database migrations

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic upgrade head
```

Expected output ends with lines like:
```
Running upgrade  -> 202501150001, Initial database schema
Running upgrade 202501150001 -> 202501160001, Add role to user_accounts ...
Running upgrade 202501160001 -> 202501170001, Backfill role ...
...
```

### 6 â€” Start all services

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  up -d
```

### 7 â€” Confirm tables exist

```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  exec postgres psql -U postgres -d provenance -c "\dt"
```

You should see `alembic_version`, `provenance_records`, and `user_accounts`. If you see "Did not find any relations", migrations did not run â€” go back to Step 4.

---

## Start / Stop / Restart

### Start everything
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  up -d
```

### Stop everything (data is kept)
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  down
```

### Restart a single service
```bash
# Backend only
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  restart backend

# Proxy only
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  restart proxy

# Neo4j only
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  restart neo4j
```

### Check what is running
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  ps
```

---

## Logs

### Stream all service logs
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs -f
```

### Backend logs (last 50 lines)
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs --tail 50 backend
```

### Proxy logs (last 50 lines)
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs --tail 50 proxy
```

### PostgreSQL logs (last 50 lines)
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs --tail 50 postgres
```

### Neo4j logs (last 50 lines)
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs --tail 50 neo4j
```

---

## Authentication

### Register your first user (becomes workspace admin)
```bash
curl -s -X POST http://localhost:8787/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!","workspace_id":"my-workspace"}' \
  | python3 -m json.tool
```

The first user registered to a workspace automatically gets `role: "admin"`. Every user registered after that gets `role: "member"`.

### Log in
```bash
curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!"}' \
  | python3 -m json.tool
```

### Save your access token to a shell variable
```bash
TOKEN=$(curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['accessToken'])")

echo "Token saved: ${TOKEN:0:40}..."
```

Use `$TOKEN` in all authenticated requests below.

### View your profile
```bash
curl -s http://localhost:8787/auth/me \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

### Refresh an expired access token
```bash
# First save the refresh token from login
REFRESH=$(curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['refreshToken'])")

curl -s -X POST http://localhost:8787/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refreshToken\":\"$REFRESH\"}" \
  | python3 -m json.tool
```

### Log out
```bash
curl -s -X POST http://localhost:8787/auth/logout \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## Team Management

### Invite a new member (admin only)
```bash
curl -s -X POST http://localhost:8787/team/invite \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"AlicePass123!","role":"member"}' \
  | python3 -m json.tool
```

Valid roles: `"admin"` or `"member"` (default is `"member"`).

### Invite an additional admin
```bash
curl -s -X POST http://localhost:8787/team/invite \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"bob","password":"BobPass123!","role":"admin"}' \
  | python3 -m json.tool
```

### List all workspace members
```bash
curl -s http://localhost:8787/team/members \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## Proxy â€” Universal LLM Capture

The proxy intercepts AI API traffic from any tool (Claude Code, Cursor, Copilot, Aider, etc.) and saves every code response to LineageLens automatically.

### Check proxy health
```bash
curl -s http://localhost:8788/proxy-health | python3 -m json.tool
```

### One-time proxy setup (after first login)

1. Log in and save your token (see Authentication above)
2. Add the token to `deploy/.env`:
   ```
   PROXY_INGEST_TOKEN=<paste your accessToken here>
   ```
3. Restart the proxy so it picks up the token:
   ```bash
   docker compose --project-name lineagelens-max \
     -f deploy/docker-compose.max.yml \
     --env-file deploy/.env \
     restart proxy
   ```
4. Confirm proxy is capturing:
   ```bash
   curl -s http://localhost:8788/proxy-health | python3 -m json.tool
   ```
   Look for `"ingest": "configured"` in the response.

### Point your AI tools at the proxy

Set these environment variables **before** starting your AI tool. They redirect all LLM API calls through LineageLens:

```bash
# For Claude Code or any Anthropic SDK tool
export ANTHROPIC_BASE_URL=http://localhost:8788

# For Cursor, Copilot, Aider, or any OpenAI SDK tool
export OPENAI_BASE_URL=http://localhost:8788
```

To make permanent (bash):
```bash
echo 'export ANTHROPIC_BASE_URL=http://localhost:8788' >> ~/.bashrc
echo 'export OPENAI_BASE_URL=http://localhost:8788' >> ~/.bashrc
source ~/.bashrc
```

To make permanent (zsh):
```bash
echo 'export ANTHROPIC_BASE_URL=http://localhost:8788' >> ~/.zshrc
echo 'export OPENAI_BASE_URL=http://localhost:8788' >> ~/.zshrc
source ~/.zshrc
```

---

## Provenance API

### Submit a provenance record (ingest)
```bash
curl -s -X POST http://localhost:8787/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workspaceId": "my-workspace",
    "filePath": "src/main.py",
    "insertedCode": "def hello():\n    return \"world\"",
    "timestampIso": "2025-01-20T10:00:00Z",
    "modelName": "claude-sonnet-4-6"
  }' \
  | python3 -m json.tool
```

### Search provenance records (semantic + keyword)
```bash
curl -s -X POST http://localhost:8787/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"authentication function","limit":10}' \
  | python3 -m json.tool
```

### View insights dashboard
```bash
curl -s http://localhost:8787/insights \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## Neo4j â€” Graph Lineage

### Open the Neo4j browser UI

Navigate to **http://localhost:7474** in your browser.

- Username: `neo4j`
- Password: value of `NEO4J_PASSWORD` from `deploy/.env`

### Open a Cypher shell
```bash
# Read NEO4J_PASSWORD from your .env file
NEO4J_PASSWORD=$(grep '^NEO4J_PASSWORD=' deploy/.env | cut -d= -f2-)

docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD"
```

Useful Cypher queries inside the shell:
```cypher
// Count all nodes
MATCH (n) RETURN count(n);

// List all node labels in the graph
CALL db.labels();

// Show up to 25 lineage nodes
MATCH (n) RETURN n LIMIT 25;

// Show relationships
MATCH (a)-[r]->(b) RETURN a, r, b LIMIT 25;

// Exit
:exit
```

### Check Neo4j health
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  ps neo4j
```

`Health` column must show `healthy` before the backend will connect to it.

---

## Health & Diagnostics

### Backend health check
```bash
curl -s http://localhost:8787/health | python3 -m json.tool
```

Expected response:
```json
{
  "status": "ok",
  "app": "LineageLens Max Backend",
  "version": "0.1.0",
  "productMode": "max",
  "environment": "development",
  "backendMode": "enterprise",
  "features": {
    "neo4j": true,
    "vectorSearch": true,
    "lineageStrictMode": false
  }
}
```

### Run the full debug checker
```bash
bash debug.sh
```

This prints PASS/FAIL for: bundle files, env variables, compose config, database connectivity, Neo4j health, migration state, and backend health.

On Windows PowerShell:
```powershell
.\debug.ps1
```

### Check which migration version is applied
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic current
```

Expected output ends with `(head)`. If not, run `alembic upgrade head`.

### Show full migration history
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic history --verbose
```

### Open a PostgreSQL shell
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  exec postgres psql -U postgres -d provenance
```

Useful SQL once inside:
```sql
-- List all tables
\dt

-- Check columns on user_accounts (confirm role column exists)
\d user_accounts

-- List all registered users
SELECT id, username, workspace_id, role, is_active, created_at FROM user_accounts;

-- Count provenance records
SELECT COUNT(*) FROM provenance_records;

-- Check applied migrations
SELECT * FROM alembic_version;

-- Exit
\q
```

---

## Reset (Wipe Everything)

```bash
# Full reset â€” removes all containers, volumes, and data (including Neo4j graph)
bash reset.sh

# Then run a fresh setup
bash quickstart.sh
```

Manual equivalent:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  down -v --remove-orphans

docker volume ls --filter name=lineagelens-max -q | xargs -r docker volume rm

rm deploy/.env
```

---

## Troubleshooting

### "Did not find any relations" â€” no tables in the database

Migrations did not run or failed silently. Fix:
```bash
# Force a fresh image build then re-run migrations
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  build backend

docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic upgrade head
```

If alembic still shows no output or errors, check backend logs:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs --tail 30 backend
```

### Register returns 500 Internal Server Error

Usually caused by a stale Docker image (from a previous version) still cached on your machine.

Fix:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  build --no-cache backend

docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  up -d
```

Then try registering again. If the second attempt returns "Username is already registered", the first attempt succeeded at DB level â€” just log in directly.

### Register returns 422 "body required"

Make sure you are passing `-H "Content-Type: application/json"` and `-d '...'` in the curl command.

### "Container name already in use"

Stop leftover containers first:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  down --remove-orphans
```

Then re-run `bash quickstart.sh`.

### Backend returns 401 "Missing bearer token"

The `$TOKEN` variable is empty or expired. Re-login and save the token again:
```bash
TOKEN=$(curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['accessToken'])")
```

### Proxy not capturing â€” "ingest: unconfigured"

`PROXY_INGEST_TOKEN` is not set in `deploy/.env`. Add your access token and restart the proxy:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  restart proxy
```

### Neo4j takes too long to start

Neo4j initialises its store on first boot and can take 60â€“90 seconds on low-core VMs. Check status:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  ps neo4j
```

Wait until `Health` shows `healthy`. If it never reaches healthy, check Neo4j logs:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  logs --tail 30 neo4j
```

### Backend cannot connect to Neo4j

The backend started before Neo4j was healthy. Restart the backend after Neo4j is healthy:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  restart backend
```

### Role column missing â€” 500 on register or login

Run migrations:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic upgrade head
```

Confirm the column exists:
```bash
docker compose --project-name lineagelens-max \
  -f deploy/docker-compose.max.yml \
  --env-file deploy/.env \
  exec postgres psql -U postgres -d provenance \
  -c "SELECT column_name FROM information_schema.columns WHERE table_name='user_accounts';"
```

`role` and `token_version` must both appear in the list.

### Old data persists after `down -v`

The volume was created under a different project name. Remove it manually:
```bash
docker volume ls | grep postgres
docker volume ls | grep neo4j
docker volume rm <volume-name-from-list>
```

Or run `bash reset.sh` which handles this automatically.
