# LineageLens — Authorization & Isolation Test Suite

I maintain LineageLens, an open-source project. I want a rigorous **test suite** that verifies our access-control boundaries hold, so contributors and self-hosting users can trust it. This is standard test engineering: write tests that assert the API rejects requests it should reject, fix any that don't, and leave the tests in the repo as regression coverage.

Everything runs in a local Docker container on an internal-only network so the test run is reproducible and self-contained.

Paste everything below the line into Antigravity.

---

## THE TASK

You are helping me build a **multi-tenant authorization test suite** for a project I own. Deliverable: pytest files added to `lineagelens-backend/tests/`, plus fixes for any boundary that a test proves is missing. Think of this as the access-control equivalent of the unit tests we already have — we assert correct behavior, and where behavior is wrong, we correct it.

### Ground rules (keep the run clean and local)
- All work happens in a throwaway `git worktree`, never the main checkout. Confirmed tests get merged by me.
- The app runs in Docker on an `internal: true` network — reproducible, no external calls, no published ports. Tests run from a sibling container.
- Fake config values only (dummy JWT secret, dummy tokens). If you notice a real secret committed anywhere, tell me the file and line — don't print it.
- Container caps: `mem_limit: 1g`, `cpus: 2`. Don't start the `proxy` service (not needed for these tests).
- The VS Code extension is out of scope for running; review its code only if relevant.
- A test only counts if it **fails against current HEAD and passes after the fix.** No test, no change.

### PART 1 — Test harness

Create the worktree:
```
git -C "C:\Users\karna\OneDrive\Desktop\Lineagelens" worktree add ../lineagelens-authztests HEAD
```

Add `lineagelens-deploy/docker-compose.authztests.yml`:

```yaml
services:
  app:
    build: { context: ../lineagelens-backend, dockerfile: Dockerfile }
    networks: [sealed]
    mem_limit: 1g
    cpus: 2
    volumes: [./authtest-data:/app/data]
    environment:
      DATABASE_URL: sqlite+aiosqlite:///./data/authtest.db
      BACKEND_MODE: solo
      NEO4J_ENABLED: "false"
      VECTOR_SEARCH_ENABLED: "false"
      EMBEDDING_PROVIDER: hash
      RATE_LIMIT_ENABLED: "true"
      APP_ENV: production
      JWT_SECRET_KEY: "test-only-jwt-secret-0000000000000000"
      REFRESH_SECRET_KEY: "test-only-refresh-secret-000000000000"
      PROXY_STATIC_TOKEN: "test-only-proxy-token-000000000000"
  tester:
    image: python:3.12-slim
    networks: [sealed]
    mem_limit: 1g
    depends_on: [app]
    volumes: [../lineagelens-backend:/work]
    working_dir: /work
    command: sleep infinity
networks:
  sealed: { internal: true }
```

Sanity-check the harness from `tester` before writing tests: `http://app:8787/health` should return OK, and an outbound connection to `1.1.1.1:53` should fail (confirms the run is self-contained). If the outbound call succeeds, fix the network config first.

### PART 2 — Seed fixture (two tenants)

Write `tests/fixtures/seed_tenants.py`: two workspaces (A, B), each with an admin/member/reviewer/viewer, and at least one object per major route family (provenance record, saved query, comment, tag, API key, webhook, policy, incident). Save every object UUID and every user's token to a fixture file the tests import.

### PART 3 — Test groups

**Group 1 — Tenant isolation (the big one).**
Parametrized test: for each route that takes an object ID, and each object owned by workspace A, assert that a workspace-B user gets 403/404 — never 2xx. Cover read/write/update/delete/export. This is a normal parametrized pytest, just wide. First, map the routes: `app/api/routes/` has 41 files; `ensure_workspace_scope` is a plain function (137 call sites), so list the routes that take an object ID but never call it or filter the query by `workspace_id`. Those are where a test is most likely to go red.

**Group 2 — Session invalidation.**
Assert that logout / password change / deactivation actually invalidates existing sessions. `get_current_auth_context` re-checks `token_version` and `is_active` against the DB. `authenticate_websocket` (security.py ~line 160) does not. Write a test: open a websocket after bumping `token_version` and setting `is_active=False` with the old token — assert it's rejected. If it connects, add the same DB check the HTTP path uses and make the test pass.

**Group 3 — Ingest token scope.**
`get_ingest_auth_context` lets `PROXY_STATIC_TOKEN` set `workspace_id` from the request body. Assert sensible handling of: a non-existent workspace ID, empty/whitespace, and an oversized body (the handler awaits `request.body()` before size checks — assert there's a bound). Add validation where a test shows it's missing.

**Group 4 — Role enforcement.**
Assert `member`/`viewer` get 403 on `require_role("admin")` routes, API keys can't exceed granted scopes, and a role downgrade takes effect on the next request (not at token expiry).

**Group 5 — Permission-visibility intent.**
`build_record_visibility_clause` treats a record with zero permission rows as visible to the whole workspace (`~any_permission_exists`). Write a test that pins the intended behavior, so removing the last permission row can't silently flip a record's visibility unnoticed.

**Group 6 — Input hardening.**
`ast_normalizer.py` parses untrusted SQL from ingest — add tests for deeply nested / very large input asserting bounded time and memory (cap the container; if a case runs away, that's the finding — add a limit). For `export_service.py` / `capsule.py`, add tests against path traversal in filenames and CSV formula injection in exports.

### PART 4 — Gate & output

Every proposed change must be backed by a test that was red before and green after. Run the full existing suite after each fix to confirm nothing broke.

Report:
1. Route inventory table (all 41 files; unscoped routes flagged).
2. Which tests were red on HEAD, with the request that triggered each.
3. The fix diff for each.
4. Severity in terms of *a self-hosted deployment with untrusted low-privilege users*.
5. Anything you investigated that turned out fine (so I know the false-positive rate).

Keep it terse — no executive summary, no severity inflation, no change without a failing test.

### PART 5 — Cleanup
```
docker compose -f docker-compose.authztests.yml down -v
git -C "C:\Users\karna\OneDrive\Desktop\Lineagelens" worktree remove ../lineagelens-authztests
```
I'll review and merge the test files myself.
