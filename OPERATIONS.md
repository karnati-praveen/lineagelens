# LineageLens Operations Runbook

Version: 0.1.0

---

## Zero-Downtime Upgrade (Plus)

LineageLens upgrades are rolling: pull new images, recreate containers one at a time, then run migrations.

```bash
# 1. Pull latest images without stopping
docker compose --project-name lineagelens-plus \
  -f lineagelens-deploy/docker-compose.plus.yml --env-file lineagelens-deploy/.env pull

# 2. Recreate containers with new images.
#    Postgres and data volumes are unchanged — data is not affected.
docker compose --project-name lineagelens-plus \
  -f lineagelens-deploy/docker-compose.plus.yml --env-file lineagelens-deploy/.env \
  up --detach --force-recreate

# 3. Run any pending database migrations (idempotent — safe to re-run)
docker exec lineagelens-plus-backend \
  sh -c 'cd /app && alembic upgrade head'

# 4. Verify health
curl -s http://localhost:8787/health | python3 -m json.tool
# Expect: "status": "ok"
```

### Upgrade checklist

- [ ] Check `CHANGELOG.md` or release notes for breaking changes before upgrading
- [ ] Back up PostgreSQL before any migration that adds/removes columns (see Backup section)
- [ ] Verify `alembic upgrade head` exits with code 0
- [ ] Confirm `/health` returns the new `version` value

---

## Zero-Downtime Upgrade (Max)

Same flow as Plus, with Neo4j included.

```bash
# 1. Pull
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env pull

# 2. Recreate (Neo4j is recreated last — graph data volume persists)
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env \
  up --detach --force-recreate

# 3. Migrate PostgreSQL
docker exec lineagelens-max-backend \
  sh -c 'cd /app && alembic upgrade head'

# 4. Verify backend + Neo4j
curl -s http://localhost:8787/health | python3 -m json.tool
# Expect: "productMode": "max", "neo4j": true
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:7474
# Expect: 200
```

---

## PostgreSQL Backup

### One-shot dump (Plus or Max)

```bash
# Dump to a timestamped file
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Plus
docker exec lineagelens-plus-postgres \
  pg_dump -U postgres -d provenance --format=custom \
  > lineagelens-provenance-plus-${TIMESTAMP}.dump

# Max
docker exec lineagelens-max-postgres \
  pg_dump -U postgres -d provenance --format=custom \
  > lineagelens-provenance-max-${TIMESTAMP}.dump
```

### Restore from dump

```bash
# Stop backend first so no writes happen during restore
docker compose --project-name lineagelens-plus \
  -f lineagelens-deploy/docker-compose.plus.yml --env-file lineagelens-deploy/.env stop backend

docker exec -i lineagelens-plus-postgres \
  pg_restore -U postgres -d provenance --clean --if-exists \
  < lineagelens-provenance-plus-20240501-120000.dump

# Restart backend
docker compose --project-name lineagelens-plus \
  -f lineagelens-deploy/docker-compose.plus.yml --env-file lineagelens-deploy/.env start backend
```

### Scheduled backup (cron example)

```cron
# Daily at 02:00 — keep last 7 dumps
0 2 * * * docker exec lineagelens-plus-postgres pg_dump -U postgres -d provenance --format=custom > /backups/provenance-$(date +\%Y\%m\%d).dump && find /backups -name "provenance-*.dump" -mtime +7 -delete
```

---

## Neo4j Backup (Max only)

### Online backup via dump

```bash
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Stop the container so Neo4j flushes to disk cleanly, then copy the volume
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

### Restore Neo4j from backup

```bash
# Stop the container
docker compose --project-name lineagelens-max \
  -f lineagelens-deploy/docker-compose.max.yml --env-file lineagelens-deploy/.env stop neo4j

# Remove existing data volume and restore from archive
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

## Neo4j Partial-Failure Reconciliation (Max only)

When the backend starts but Neo4j is temporarily unreachable, provenance records are stored in
PostgreSQL without a corresponding lineage graph node. These records have `lineage_node_id = NULL`.

The backend logs a warning per record: `Neo4j lineage is unavailable; record stored without graph lineage.`

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

Orphaned records do NOT need to be deleted — they are fully queryable in PostgreSQL and the
dashboard. Graph lineage is additive. To backfill lineage nodes after Neo4j recovers:

1. Confirm Neo4j is healthy: `curl -s http://localhost:7474`

2. Re-POST each orphaned record to `/ingest`. The backend will detect the existing PostgreSQL
   record via UUID deduplication and skip storing a duplicate; it will still attempt to write
   the Neo4j lineage node if one is missing. (Requires a migration to expose this backfill
   path — see roadmap.)

3. Until a dedicated backfill endpoint ships, the manual path is: query orphaned UUIDs,
   fetch each from `/provenance/{uuid}`, and re-POST the `provenance_payload` to `/ingest`.

```bash
TOKEN=<your-jwt>

# Fetch a list of orphaned UUIDs from the DB
UUIDS=$(docker exec lineagelens-max-postgres \
  psql -U postgres -d provenance -t \
  -c "SELECT uuid FROM provenance_records WHERE lineage_node_id IS NULL LIMIT 100;" \
  | tr -d ' ')

for UUID in $UUIDS; do
  # Fetch the stored payload
  RECORD=$(curl -s -H "Authorization: Bearer $TOKEN" \
    http://localhost:8787/provenance/$UUID)

  PAYLOAD=$(echo "$RECORD" | python3 -c "import sys,json; r=json.load(sys.stdin); print(json.dumps(r.get('record', {})))")

  # Re-ingest (deduplication skips the DB insert, Neo4j write is retried)
  curl -s -X POST http://localhost:8787/ingest \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $TOKEN" \
    -d "$PAYLOAD" | python3 -m json.tool
done
```

### Prevention

Set `LINEAGE_STRICT_MODE=true` in `lineagelens-deploy/.env` to make the backend refuse to start if Neo4j
is unreachable. This prevents any records being written without a corresponding lineage node,
at the cost of hard failing instead of degrading gracefully.

---

## Rate Limiter: Single-Replica Limitation

The default rate limiter (`InMemoryRateLimiter`) is **process-local**. This means:

- In a single-container deployment (the standard deployment), it works correctly.
- If you run multiple backend replicas behind a load balancer, each replica tracks its own
  counter independently — a single client can make N×limit requests by hitting N replicas.

### Detecting the issue

```bash
# If you see different rate limit counters from multiple backend instances, you have this problem
curl -s http://backend-1:8787/health
curl -s http://backend-2:8787/health
```

### Fix: enable Redis-backed rate limiting

Set `REDIS_URL` in `lineagelens-deploy/.env`:

```bash
REDIS_URL=redis://redis:6379/0
```

Add a Redis service to your compose file, then restart the backend. The backend auto-detects
`REDIS_URL` at startup and switches to the shared Redis limiter. See the [Redis Rate Limiter](#)
section in the codebase for details (`lineagelens-backend/app/core/rate_limit_redis.py`).

The in-memory limiter remains the default — zero dependencies for a single-replica deployment.

---

## Environment Variable Reference

> **Note on `.env` names vs. proxy names.** The table below lists the variables the proxy process
> reads directly. In the Docker Compose deployment you edit `lineagelens-deploy/.env`, which uses
> *compose-mapped* names: `PROXY_INGEST_TOKEN` (mapped to `BACKEND_INGEST_TOKEN` **and**
> `PROXY_STATIC_TOKEN`) and `PROXY_UPSTREAM_URL` (mapped to `UPSTREAM_URL`). Set the `PROXY_*` names
> in `.env`; the raw names below apply only when running the proxy directly without Compose.

### Proxy

| Variable | Default | Description |
|----------|---------|-------------|
| `UPSTREAM_URL` | `https://api.anthropic.com` | Upstream LLM API to forward requests to |
| `BACKEND_URL` | `http://backend:8787` | LineageLens backend base URL |
| `BACKEND_INGEST_TOKEN` | _(required)_ | JWT used by the proxy to POST to `/ingest` |
| `PROXY_WORKSPACE_ID` | `proxy-capture` | Workspace ID assigned to proxy-captured records |
| `PROXY_PORT` | `8788` | Port the HTTP proxy listens on |
| `PROXY_HOST` | `0.0.0.0` | Bind address |
| `PROXY_MAX_BODY_BYTES` | `2000000` | Maximum request body the proxy will accept |
| `PROXY_REDACT_PATTERNS` | _(empty)_ | Comma-separated regex patterns redacted from captured content before ingest. Example: `Bearer [A-Za-z0-9._-]+,sk-[A-Za-z0-9]+` |
| `PROXY_CONNECT_PORT` | `8789` | Port the HTTPS CONNECT tunnel server listens on |
| `PROXY_CA_CERT_PATH` | _(empty)_ | Path to CA certificate PEM file for HTTPS CONNECT MITM. If unset, CONNECT falls back to transparent TCP relay. |
| `PROXY_CA_KEY_PATH` | _(empty)_ | Path to CA private key PEM file (paired with `PROXY_CA_CERT_PATH`) |

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | _(empty)_ | Redis connection URL. When set, enables shared rate limiting across replicas. Example: `redis://localhost:6379/0` |
| `LINEAGE_STRICT_MODE` | `false` | If `true`, the backend refuses to start when Neo4j is unreachable |
| `RATE_LIMIT_ENABLED` | `true` | Enable or disable the HTTP rate limiter |
| `RATE_LIMIT_MAX_REQUESTS` | `120` | Requests per window per client IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit sliding window in seconds |
