# LineageLens Enterprise — Command Reference

All commands are run from the **root of this folder** (where `quickstart.sh` lives).

Variables used throughout:
```
PROJECT=lineagelens-enterprise
COMPOSE_FILE=deploy/docker-compose.enterprise.yml
ENV_FILE=deploy/.env
API=http://localhost:8787
NEO4J_BROWSER=http://localhost:7474
```

---

## First-Time Setup

```bash
# Full automated setup (does everything below in order)
bash quickstart.sh
```

---

## Database Setup (step by step)

### 1 — Start PostgreSQL and Neo4j only
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  up -d postgres neo4j
```

### 2 — Wait until PostgreSQL is ready
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  exec postgres pg_isready -U postgres -d provenance
```
Re-run until you see: `provenance:5432 - accepting connections`

### 3 — Wait until Neo4j is ready (first boot takes ~60s)
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  ps neo4j
```
Wait until `Health` column shows `healthy`.

### 4 — Apply all migrations
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic upgrade head
```

### 5 — Check current migration version
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic current
```

### 6 — Show full migration history
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic history --verbose
```

### 7 — Open a PostgreSQL shell
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  exec postgres psql -U postgres -d provenance
```

Useful SQL inside the shell:
```sql
-- Check tables
\dt

-- Confirm 'role' column exists
\d user_accounts

-- List all users
SELECT id, username, workspace_id, role, is_active, created_at FROM user_accounts;

-- List alembic migration history
SELECT * FROM alembic_version;

-- Exit
\q
```

### 8 — Open a Neo4j Cypher shell
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD"
```

Useful Cypher inside the shell:
```cypher
// Count all nodes
MATCH (n) RETURN count(n);

// List node labels
CALL db.labels();

// List all lineage nodes
MATCH (n) RETURN n LIMIT 25;

// Exit
:exit
```

Or use the browser UI at **http://localhost:7474** (username: `neo4j`).

---

## Start / Stop / Restart

### Start all services
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  up -d
```

### Stop all services (keep data)
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  down
```

### Restart backend only
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  restart backend
```

### Check running containers
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  ps
```

---

## Logs

### Stream all logs
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  logs -f
```

### Backend logs only (last 50 lines)
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  logs --tail 50 backend
```

### PostgreSQL logs only
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  logs --tail 50 postgres
```

### Neo4j logs only
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  logs --tail 50 neo4j
```

---

## Reset (Wipe Everything)

```bash
# Full reset — deletes all containers, volumes, and data
bash reset.sh

# Then start fresh
bash quickstart.sh
```

Manual equivalent:
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  down -v --remove-orphans

# Remove any leftover volumes
docker volume ls --filter name=lineagelens-enterprise -q | xargs -r docker volume rm

# Remove secrets
rm deploy/.env
```

---

## API — Authentication

### Register (first user becomes admin)
```bash
curl -s -X POST http://localhost:8787/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!","workspace_id":"my-workspace"}' \
  | python3 -m json.tool
```

### Login
```bash
curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!"}' \
  | python3 -m json.tool
```

### Save token to variable
```bash
TOKEN=$(curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourPassword123!"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['accessToken'])")

echo "Token: $TOKEN"
```

### View your profile
```bash
curl -s http://localhost:8787/auth/me \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

### Refresh access token
```bash
curl -s -X POST http://localhost:8787/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refreshToken\":\"$REFRESH_TOKEN\"}" \
  | python3 -m json.tool
```

---

## API — Team Management

### Invite a new member (admin only)
```bash
curl -s -X POST http://localhost:8787/team/invite \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"bob","password":"BobPass123!","role":"member"}' \
  | python3 -m json.tool
```

### List team members
```bash
curl -s http://localhost:8787/team/members \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## API — Health & Diagnostics

### Debug the bundle
```bash
bash debug.sh
```
This prints PASS/FAIL for bundle files, env variables, compose parsing, database health, Neo4j health, Alembic migration checks, and backend health.

On Windows PowerShell:
```powershell
.\debug.ps1
```

### Health check (shows neo4j and vector search status)
```bash
curl -s http://localhost:8787/health | python3 -m json.tool
```

### Search provenance records
```bash
curl -s "http://localhost:8787/search?q=your+query&limit=10" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
```

---

## Troubleshooting

### Backend starts but register returns empty response
The `role` column is missing from the database. Run migrations:
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic upgrade head
```

### Container name already in use
Stop existing containers first:
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  down --remove-orphans
```
Then re-run `quickstart.sh` or `up -d`.

### Old data appears after docker compose down -v
The volume was created under a different project name. Remove it manually:
```bash
docker volume ls | grep postgres
docker volume ls | grep neo4j
docker volume rm <volume-name>
```
Or run `bash reset.sh` which handles this automatically.

### Neo4j takes too long to start
Neo4j initialises its store on first boot and can take 60–90 seconds.
Check its health status:
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  ps neo4j
```
Wait until `Health` shows `healthy` before running migrations.

### Check if role column exists
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  exec postgres psql -U postgres -d provenance \
  -c "SELECT column_name FROM information_schema.columns WHERE table_name='user_accounts';"
```
`role` must appear in the list. If missing, run `alembic upgrade head`.

### See which migration is currently applied
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  --env-file deploy/.env \
  run --rm --no-deps backend alembic current
```
Expected output should end with `(head)`. If not, run `alembic upgrade head`.

### Neo4j connection refused in backend logs
Neo4j was not healthy when the backend started. Restart the backend after Neo4j is healthy:
```bash
docker compose --project-name lineagelens-enterprise \
  -f deploy/docker-compose.enterprise.yml \
  restart backend
```
