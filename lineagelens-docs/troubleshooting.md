# LineageLens Troubleshooting

Last reviewed: 2026-06-10

---

## Status bar stays `LL: Easy` after starting the proxy

The extension polls `GET http://localhost:8788/proxy-health` every 30 s. If it gets a `{"status":"ok"}` response, the status bar switches to `LL: Power` automatically — no restart needed.

**Common causes:**

**1. The env var was not exported in the shell that launched VS Code (or Cursor).**

Setting `ANTHROPIC_BASE_URL` in a terminal does not affect VS Code if it was opened from the system launcher (dock, taskbar, Spotlight, etc.). The extension reads `lineagelensBase.proxyUrl` from VS Code settings, not the shell environment.

Fix: either set `lineagelensBase.proxyUrl` to `http://localhost:8788` in VS Code settings, or always launch VS Code from a terminal where the env var is already exported:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8788
code .
```

**2. The proxy is not actually running.**

```bash
curl -s http://localhost:8788/proxy-health
```

If this times out or returns a connection refused error, the proxy is not listening. Check that the Docker container or the `uvicorn` process is up:

```bash
docker ps | grep lineagelens-proxy
# or, for a native install:
ps aux | grep proxy.py
```

**3. The proxy is running on a non-default port.**

If `PROXY_PORT` was changed, update `lineagelensBase.proxyUrl` in VS Code settings to match (e.g., `http://localhost:8790`).

---

## 401 on `POST /ingest`

The `/ingest` endpoint accepts two kinds of Bearer tokens:

- A regular JWT issued by `/auth/login` (for the VS Code extension)
- The proxy static token (`PROXY_STATIC_TOKEN` env var on the backend)

**Token mismatch — proxy path:**

The proxy sends `BACKEND_INGEST_TOKEN` (its env var) as the Bearer token. The backend checks it against its own `PROXY_STATIC_TOKEN` env var. These two values must be identical.

Check:
```bash
# On the proxy container / process:
echo $BACKEND_INGEST_TOKEN

# On the backend container / process:
echo $PROXY_STATIC_TOKEN
```

If they differ, update the mismatched value and restart the affected container.

**Expired JWT — extension path:**

The access token has a 30-minute TTL by default. The extension refreshes it automatically before expiry. If you see persistent 401s from the extension, check that `lineagelensBase.ingestToken` in VS Code settings has not been overridden with a stale static token.

**Wrong workspace scope:**

The ingest payload must include `workspaceId` and it must match the workspace the token is scoped to. If they differ, the backend returns 403 (`Workspace scope mismatch`), which the extension logs as an ingest failure.

---

## Port conflicts (8787 / 8788 / 8789)

The default port assignments are:

| Port | Service |
|------|---------|
| 8787 | LineageLens backend (FastAPI) |
| 8788 | LineageLens proxy (LLM forward proxy) |
| 8789 | CONNECT tunnel (for `HTTPS_PROXY` clients) |

If something else is already on one of these ports, the Docker Compose startup fails silently or the process binds to the wrong address.

Find what is using a port:

```bash
# Linux / macOS
lsof -i :8787

# Windows (PowerShell)
netstat -ano | Select-String ":8787"
```

To run on different ports, edit `lineagelens-deploy/docker-compose.<tier>.yml` and update the port mappings. Also update `PROXY_PORT` (proxy), the backend's `uvicorn` bind port, and any `BACKEND_URL` / `ANTHROPIC_BASE_URL` references in `.env`.

---

## Docker container reports as unhealthy

Docker Compose healthchecks are defined in the compose files. A container shows `(unhealthy)` in `docker ps` when its healthcheck command exits non-zero repeatedly.

**Read the logs first:**

```bash
docker logs lineagelens-backend --tail 100
docker logs lineagelens-proxy --tail 100
```

Common causes and fixes:

| Log message | Cause | Fix |
|---|---|---|
| `JWT_SECRET_KEY must be at least 32 characters` | `.env` is missing or has the placeholder value | Set a real secret in `.env` |
| `database connection refused` | Postgres container not yet ready | Wait ~10 s; Docker Compose `depends_on` has a 30 s health grace period |
| `BACKEND_MODE must be 'solo', 'team', or 'enterprise'` | Typo in `BACKEND_MODE` env var | Correct the value |
| `Port already in use` | Port conflict (see above) | Change the port mapping |
| `VECTOR_SEARCH_ENABLED requires PostgreSQL` | SQLite + vector search combo | Set `VECTOR_SEARCH_ENABLED=false` for Lite |

After fixing the root cause, restart the container:

```bash
docker compose -f lineagelens-deploy/docker-compose.plus.yml restart backend
```

---

## SQLite locked errors (Lite)

SQLite allows only one writer at a time. Under normal single-user load this is never an issue. It appears when:

- Two processes are writing simultaneously (e.g., the proxy and a direct extension ingest both hit the backend at the same moment).
- The VS Code extension retries an ingest while a previous attempt is still executing.

The backend uses `aiosqlite` with WAL mode, which tolerates concurrent readers. If you see `database is locked` in the logs, check:

1. Only one backend container is running (`docker ps`). Two instances sharing the same SQLite file will fight.
2. The SQLite file is not on a network drive (NFS, Samba, OneDrive sync). SQLite locking does not work correctly over network filesystems.

If the problem persists, upgrade to Plus (PostgreSQL handles concurrent writes cleanly).

---

## Neo4j takes 60 seconds to become ready (Max)

Neo4j 5+ takes roughly 60 s to finish its startup sequence on first boot, or after a container restart with an empty data directory. The backend logs:

```
Neo4j is unavailable; continuing without lineage: ...
```

This is expected — the backend starts and serves traffic while Neo4j finishes booting. Once Neo4j is ready, the next request that needs it will succeed. The `LINEAGE_STRICT_MODE=true` env var turns this into a fatal error if you need the backend to refuse traffic until Neo4j is ready.

If Neo4j never becomes healthy after several minutes:

```bash
docker logs lineagelens-neo4j --tail 50
```

Common issues: not enough memory (Neo4j requires at least 1 GB heap), or the `NEO4J_PASSWORD` in `.env` does not match the password the container was initially started with (password is set once on first boot; to reset it, delete the Neo4j data volume and restart).

---

## Redirected to `/setup` in a loop

The `SetupGuardMiddleware` redirects every request to `/setup` until at least one `UserAccount` row exists in the database. If you land on `/setup` but the page itself keeps redirecting, the most common causes are:

**1. No admin user has been created yet.**

Complete the setup form at `http://localhost:8787/setup`. The form calls `POST /setup` which creates the first admin; after that the guard caches `setup_complete = True` and stops redirecting.

Alternatively, set `ADMIN_SEED_USERNAME` and `ADMIN_SEED_PASSWORD` in `.env` before first boot. The backend creates the admin account on startup and skips the setup wizard entirely.

**2. The database was reset but the backend process is still running.**

The guard caches `setup_complete = True` in memory after the first successful check. If the database is wiped without restarting the backend, the cache is stale and the guard stops redirecting even though no users exist. Restart the backend container to clear the cache:

```bash
docker compose -f lineagelens-deploy/docker-compose.plus.yml restart backend
```

**3. The backend cannot reach the database.**

The guard's DB check catches exceptions and redirects to `/setup` as a safe fallback. If every request is hitting this path, check that the database container is healthy and the `DATABASE_URL` is correct.

The following paths always bypass the guard regardless of setup state: `/setup`, `/health`, `/auth/login`, `/auth/register`, `/auth/sso/callback`, `/invite-accept`.

---

## Windows-specific notes

The quickstart and reset scripts (`lineagelens-scripts/*.sh`) are bash scripts. They do not run natively in PowerShell or CMD.

Run them in one of:

- **WSL 2** (recommended) — full Linux environment, Docker Desktop WSL backend works seamlessly
- **Git Bash** — ships with Git for Windows; sufficient for the quickstart scripts
- **MSYS2 / Cygwin** — also work but add extra setup

Example using Git Bash:

```bash
# Open Git Bash, then:
bash lineagelens-scripts/quickstart-lite.sh
```

The Python backend and proxy run normally in Windows PowerShell or WSL once the Docker containers are up. The VS Code extension is platform-agnostic.

**Path separators:** If you set `lineagelensBase.backendUrl` or `ANTHROPIC_BASE_URL` in Windows environment variables, use forward slashes or the standard `http://` URL form — no backslashes.

**Firewall:** Windows Defender may block the proxy port (8788) on first run. If the extension cannot reach the proxy, check Windows Firewall inbound rules and allow `uvicorn` / Docker for the relevant port.
