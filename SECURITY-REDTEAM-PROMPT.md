# LineageLens Red-Team Prompt — Fully Sandboxed, Laptop-Safe

Defensive security review of your own code. Everything runs inside Docker on an **egress-blocked internal network**. Nothing touches the host OS, host network, real credentials, or the real git repo.

Paste everything below the line into Antigravity.

---

## THE PROMPT

You are the **Orchestrator** of a defensive security review of LineageLens, a project I own and maintain. Goal: find and fix real vulnerabilities before open-source users hit them. Output is **fixes plus regression tests**, not a findings report.

---

## PART 0 — HOST SAFETY CONTRACT (non-negotiable)

Violating any of these aborts the run immediately. State that you have read and accepted this contract before your first action.

**Never touch the host:**
1. No process runs outside Docker except `docker`, `git worktree`, and file edits inside the worktree.
2. No writes anywhere except the git worktree created in Part 1 and `./redteam/` inside it. Never write to `C:\Users\karna\...` outside that worktree.
3. **Never touch the real `.git`** of `C:\Users\karna\OneDrive\Desktop\Lineagelens`. No `commit`, `push`, `reset`, `clean`, `checkout`, or history rewrite on the primary repo. Work only in the worktree.
4. **Never touch Windows Credential Manager / keytar.** The VS Code extension is OUT OF SCOPE for dynamic testing. Static code review of the extension is allowed; running it is not.
5. No real secrets. Every credential in the sandbox is a fake constant. If you find a real secret in the repo or in git history, **report the file and line — do not print the value**.
6. **No network target except the sandbox containers.** No scanning, no requests to any host, domain, or IP outside the internal Docker network. Not localhost services outside the compose stack, not the LAN, not the internet.
7. **Do not start the `proxy` service.** Its `UPSTREAM_URL` defaults to `https://api.anthropic.com`. It stays down for the entire run.
8. No `docker run` with `--privileged`, `--network host`, `--pid host`, or host path mounts outside the worktree.
9. No modification of Docker Desktop settings, Windows firewall, hosts file, environment variables, or any system configuration.
10. Resource caps on every container: `mem_limit: 1g`, `cpus: 2`. If a DoS test starts consuming the host, kill the container — never let it run unbounded.
11. Full teardown at the end: `docker compose down -v` and remove the worktree.

**Scope limit:** the only permitted target is the LineageLens stack you start in Part 1. Everything else — my machine, my network, any third party — is off-limits and there is no exception or justification that changes this.

---

## PART 1 — Build the sandbox

Create an isolated copy. Do not work in the main checkout:

```
git -C "C:\Users\karna\OneDrive\Desktop\Lineagelens" worktree add ../lineagelens-redteam HEAD
```

Inside the worktree, create `lineagelens-deploy/docker-compose.redteam.yml`:

```yaml
services:
  app:
    build:
      context: ../lineagelens-backend
      dockerfile: Dockerfile
    networks: [sealed]
    mem_limit: 1g
    cpus: 2
    volumes:
      - ./redteam-data:/app/data
    environment:
      DATABASE_URL: sqlite+aiosqlite:///./data/redteam.db
      BACKEND_MODE: solo
      NEO4J_ENABLED: "false"
      VECTOR_SEARCH_ENABLED: "false"
      EMBEDDING_PROVIDER: hash
      RATE_LIMIT_ENABLED: "true"
      APP_ENV: production
      JWT_SECRET_KEY: "FAKE-redteam-jwt-secret-not-real-0000000000"
      REFRESH_SECRET_KEY: "FAKE-redteam-refresh-secret-not-real-000000"
      PROXY_STATIC_TOKEN: "FAKE-proxy-static-token-for-testing-only"

  tester:
    image: python:3.12-slim
    networks: [sealed]
    mem_limit: 1g
    depends_on: [app]
    volumes:
      - ../lineagelens-backend:/work
    working_dir: /work
    command: sleep infinity

networks:
  sealed:
    internal: true      # <-- no route off this network. SSRF and exfil fail closed.
```

`internal: true` is the safety mechanism: containers reach each other and **nothing else**. No LAN, no internet, no DNS out. It also means no published ports — so you run tests from the `tester` container, not from the host. That is intentional.

Verify isolation before proceeding. Run from `tester`:
```
python -c "import socket;socket.create_connection(('1.1.1.1',53),3)"   # MUST fail
python -c "import urllib.request;urllib.request.urlopen('http://app:8787/health',timeout=5).read()"   # MUST succeed
```
**If the first command succeeds, the network is not sealed. STOP and fix it before any testing.**

## PART 1B — Seed two tenants

Write `redteam/seed.py`, run inside `tester`, creating:

- Workspace **W-A** and workspace **W-B**
- In each: an `admin`, a `member`, a `reviewer`, a `viewer`
- In each: provenance records, saved queries, comments, tags, API keys, webhooks, policies, incidents — at least one object per major route family
- Record every object UUID and every user's token in `redteam/fixtures.json`

Without two fully populated tenants the isolation matrix cannot run. This is the highest-value 80 lines in the whole exercise.

---

## PART 2 — Agents

Spawn each in its own worktree, isolated. All bound by Part 0.

### Agent RECON — read-only, no execution
Build a table covering every route in `app/api/routes/` (41 files): path, method, auth dependency used, role dependency, whether `ensure_workspace_scope` is called, whether the DB query filters on `workspace_id`, and which path params accept object IDs.

`ensure_workspace_scope` is an ordinary function, not a dependency — it must be called explicitly and there are 137 call sites. **Your deliverable is the list of routes that accept an object ID and do NOT scope it.** That list is the target set for TENANT.

### Agent TENANT — highest expected yield
For every route × every object ID belonging to W-A, authenticate as each W-B role and issue the request. **Any 2xx is a finding.** Repeat inverted (W-B objects, W-A creds). Cover read, write, update, delete, and export/bulk paths. Log the full matrix including passes.

### Agent AUTHZ
Four specific targets, drawn from reading `app/core/security.py`:

1. **Websocket revocation gap.** `get_current_auth_context` verifies `token_version` against the DB and checks `is_active`. `authenticate_websocket` (line ~160) does neither. Test: open a WS with a valid token, bump the user's `token_version` (logout/password change), set `is_active=False`, then open a new WS with the same token. **If it connects, confirmed.** Hits `ws_capture.py` and `websocket_manager.py`.
2. **Proxy static token blast radius.** `get_ingest_auth_context` lets a valid `PROXY_STATIC_TOKEN` bypass user auth and take `workspace_id` from the request body. Test: ingest into W-A, into W-B, into a workspace that does not exist, into an empty/whitespace/oversized/unicode workspace ID. Also send a 100MB body — the dependency awaits `request.body()` before any size check.
3. **Role escalation.** Can `member` reach `require_role("admin")` routes? Can an API key exceed its granted scopes? Does a role downgrade take effect immediately or only after token expiry?
4. **Default-open visibility.** `build_record_visibility_clause` returns `or_(user_permission_exists, ~any_permission_exists)` — a record with zero permission rows is visible to the whole workspace. Test whether deleting the last permission row *opens* a previously restricted record. Determine whether this is intended; either way it needs a test pinning the behavior.

### Agent INPUT
- `ast_normalizer.py` parses untrusted SQL from ingest. Deep nesting, 10k-term unions, pathological regex input, unicode, null bytes. Watch for CPU/stack blowup. **Cap the container and kill it rather than letting it run away.**
- Verify no normalizer output ever reaches a query as a string.
- `export_service.py` / `capsule.py`: zip-slip on capsule build/extract, path traversal in filenames, CSV formula injection in exports.
- `webhooks.py`: SSRF attempts. On the sealed network these must fail at the socket — confirm the app handles that gracefully rather than leaking internals in an error.

### Agent SUPPLY — static only
Dependency CVEs (backend `requirements`, extension `package-lock.json`), Dockerfile running as root, `.env.*.example` files containing anything resembling a real secret, and `git log -p` scanned for committed credentials. **Report locations, never values.** Static review only — do not execute extension code.

### Agent TRIAGE — the gate
**No finding is real without a pytest that fails on current HEAD and passes after the fix.** Tests go in `lineagelens-backend/tests/` alongside the existing suite and must run against the sealed stack. Anything that cannot be expressed as a reproducing test is dropped, however convincing the writeup. Confirm the full existing suite still passes after every fix.

---

## PART 3 — Output

**A. Route inventory table** — all 41 files, with the unscoped-route list called out.

**B. Isolation matrix** — every route × role × cross-tenant attempt, pass and fail.

**C. Confirmed findings only** — for each: the failing test file, the exact request that triggers it, the fix diff, and the severity *for a self-hosted deployment with untrusted low-privilege users* (your actual threat model — not "anonymous internet attacker").

**D. Dropped findings** — everything TRIAGE killed and why. I want to see the false-positive rate.

**E. Safety attestation** — confirm the sealed-network check passed, no host writes occurred outside the worktree, the proxy service never started, no real credentials were used or printed, and teardown completed.

### FORBIDDEN
No executive summary. No CVSS theater. No severity inflation. No finding without a failing test. No speculative "an attacker could potentially" — either the test goes red or it isn't a finding. No fixes that break existing tests.

---

## PART 4 — Teardown

```
docker compose -f docker-compose.redteam.yml down -v
git -C "C:\Users\karna\OneDrive\Desktop\Lineagelens" worktree remove ../lineagelens-redteam
```

Confirmed test files get cherry-picked to a branch on the main repo **by me, not by you.**

---

## Why this is safe on your machine

| Risk | Control |
|---|---|
| Agent attacks your network | `internal: true` — no route off the Docker network, verified before testing starts |
| SSRF reaches your router or cloud metadata | Same. Fails at the socket. |
| Runaway fuzzer eats your laptop | `mem_limit: 1g`, `cpus: 2`, kill-on-runaway instruction |
| Real credentials leak | Fake constants only; found secrets reported by location, never value |
| Your keychain gets touched | Extension excluded from dynamic testing entirely |
| Your repo gets mangled | All work in a `git worktree`; main checkout is read-only to the agents |
| Traffic hits a third party | `proxy` service never starts |
| Leftover state | `down -v` plus worktree removal |

Run `docker compose down -v` yourself if anything looks wrong. Nothing here survives it.
