# LineageLens API Reference

Last reviewed: 2026-06-10

This is a human-curated guide to the most important endpoint groups. For the full machine-readable schema run the export script from the repo root:

```bash
python lineagelens-scripts/export-openapi.py
# writes lineagelens-docs/openapi.json
```

The interactive Swagger UI is available at `http://localhost:8787/docs` when `APP_ENV` is not `production`.

---

## Authentication model

Every request except `/health`, `/setup`, `/auth/login`, `/auth/register`, and `/auth/sso/callback` requires a Bearer token in the `Authorization` header:

```
Authorization: Bearer <accessToken>
```

The proxy uses a separate static token (`PROXY_STATIC_TOKEN` on the backend, matching `BACKEND_INGEST_TOKEN` in the proxy config) for the `/ingest` endpoint only. This avoids issuing a full user JWT to the proxy process.

---

## Endpoint groups

- [Auth](#auth)
- [Ingest](#ingest)
- [Search](#search)
- [Provenance](#provenance)
- [Integrity](#integrity)
- [Export / Agent Trace](#export--agent-trace)
- [Health](#health)

---

## Auth

**Tiers:** Lite · Plus · Max

### `POST /auth/register`

Creates a new workspace and an admin user in one call. Returns an access token and a refresh token.

Self-registration must be enabled (`REGISTRATION_ENABLED=true`, the default). On a shared instance where you want to control who can join, disable it and use invite links instead.

```bash
curl -s -X POST http://localhost:8787/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "password": "MyStr0ng!Pass",
    "workspaceId": "acme-eng"
  }' | jq .
```

Example response:

```json
{
  "accessToken": "eyJ...",
  "refreshToken": "eyJ...",
  "tokenType": "bearer",
  "expiresInSeconds": 1800,
  "expiresAtIso": "2026-06-10T15:00:00+00:00",
  "workspaceId": "acme-eng",
  "user": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "username": "alice",
    "workspaceId": "acme-eng",
    "role": "admin"
  }
}
```

Password rules: ≥8 chars, at least one uppercase, one lowercase, one digit, one special character.

---

### `POST /auth/login`

```bash
curl -s -X POST http://localhost:8787/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "MyStr0ng!Pass"}' | jq .
```

Returns the same `AuthTokenResponse` shape as `/auth/register`.

---

### `POST /auth/refresh`

Exchange a valid refresh token for a new access + refresh token pair. The previous refresh token is invalidated (rotation).

```bash
curl -s -X POST http://localhost:8787/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refreshToken": "<refresh_token>"}' | jq .
```

---

### `POST /auth/logout`

Increments the token version, invalidating all outstanding tokens for the user.

```bash
curl -s -X POST http://localhost:8787/auth/logout \
  -H "Authorization: Bearer <access_token>"
```

Response: `{"loggedOut": true}`

---

### `GET /auth/me`

Returns the current user's identity and role.

```bash
curl -s http://localhost:8787/auth/me \
  -H "Authorization: Bearer <access_token>" | jq .
```

Example response:

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "username": "alice",
  "workspaceId": "acme-eng",
  "role": "admin",
  "scopes": ["provenance:read", "provenance:write"]
}
```

---

### `POST /auth/invite` (admin) · `POST /auth/invite/accept`

**Tiers:** Plus · Max (requires Redis or in-memory KV store; works in Lite but tokens are lost on restart)

Admins generate a one-time invite link. New users accept it to join the workspace without needing self-registration to be open.

```bash
# Admin creates an invite (expires in 60 min, up to 5 uses, role=member)
curl -s -X POST http://localhost:8787/auth/invite \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"workspaceId": "acme-eng", "ttlMinutes": 60, "maxUses": 5, "role": "member"}' | jq .
```

```json
{
  "token": "abc123xyz...",
  "workspaceId": "acme-eng",
  "role": "member",
  "maxUses": 5,
  "expiresAt": "2026-06-10T16:00:00+00:00"
}
```

```bash
# New user accepts the invite
curl -s -X POST http://localhost:8787/auth/invite/accept \
  -H "Content-Type: application/json" \
  -d '{
    "token": "abc123xyz...",
    "username": "bob",
    "password": "MyStr0ng!Pass"
  }' | jq .
```

Returns an `AuthTokenResponse` — the new user is logged in immediately.

---

## Ingest

**Tiers:** Lite · Plus · Max

### `POST /ingest`

Ingests a provenance event. Called by the proxy (using its static token) and by the VS Code extension (using a user JWT). The payload is flexible — the normalizer accepts several shapes — but the fields below are the most useful ones to send directly.

```bash
TOKEN="<access_token_or_proxy_static_token>"

curl -s -X POST http://localhost:8787/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workspaceId": "acme-eng",
    "filePath": "src/routes/auth.py",
    "insertedCode": "def rate_limit(request):\n    ...",
    "modelName": "claude-opus-4-5",
    "promptMessages": [
      {"role": "user", "content": "add rate limiting to /api/login"}
    ],
    "source": {
      "toolName": "claude-code",
      "captureStatus": "full"
    }
  }' | jq .
```

To avoid duplicate records on retry, pass `X-Idempotency-Key: <uuid>`. If a record with that UUID already exists, the existing record is returned and `stored: false`.

Example response:

```json
{
  "uuid": "7b2a8e4f-1c3d-4a5b-8e9f-0d1c2b3a4e5f",
  "workspaceId": "acme-eng",
  "lineageNodeId": null,
  "stored": true,
  "warnings": []
}
```

`lineageNodeId` is non-null only on Max (Neo4j enabled).

---

### `WS /ws/capture`

WebSocket alternative to `POST /ingest`. The VS Code extension tries this first; falls back to HTTP if the connection fails. Pass the JWT as a query parameter:

```
ws://localhost:8787/ws/capture?token=<access_token>
```

Send the same JSON payload shape as `/ingest` as individual text frames. The server responds with an acknowledgement frame containing the record UUID.

---

## Search

**Tiers:** Plus · Max only (returns `403` on Lite)

### `POST /search`

Keyword search across provenance records in the workspace. Returns paginated results with snippets.

```bash
curl -s -X POST http://localhost:8787/search \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspaceId": "acme-eng",
    "query": "rate limiting auth",
    "limit": 10,
    "offset": 0
  }' | jq .
```

Example response:

```json
{
  "results": [
    {
      "uuid": "7b2a8e4f-...",
      "score": 0.87,
      "model": "claude-opus-4-5",
      "timestampIso": "2026-06-10T14:22:11+00:00",
      "filePath": "src/routes/auth.py",
      "snippet": "def rate_limit(request):\n    ...",
      "record": { "...full record..." }
    }
  ],
  "count": 1,
  "total": 1,
  "offset": 0,
  "limit": 10,
  "has_more": false,
  "next_cursor": null,
  "warnings": []
}
```

Additional filter fields: `modelName`, `filePath`, `riskLevel` (`low`/`medium`/`high`/`critical`), `dateFrom`, `dateTo`, `captureStatus` (`full` or `file_diff`).

---

### `GET /search/facets`

Returns aggregated counts for the filter UI: models, risk levels, file extensions, capture status.

```bash
curl -s http://localhost:8787/search/facets \
  -H "Authorization: Bearer <access_token>" | jq .
```

```json
{
  "model_name": [{"value": "claude-opus-4-5", "count": 42}],
  "risk_level": [{"value": "medium", "count": 30}, {"value": "high", "count": 12}],
  "capture_status": [{"value": "captured", "count": 40}, {"value": "uncaptured", "count": 2}],
  "file_extension": [{"value": ".py", "count": 28}]
}
```

---

## Provenance

**Tiers:** Lite · Plus · Max

### `GET /provenance`

List provenance records for the workspace, newest first. Supports `limit` (1–200, default 20) and `offset`.

```bash
curl -s "http://localhost:8787/provenance?limit=5&offset=0" \
  -H "Authorization: Bearer <access_token>" | jq .
```

Response shape: `{results: [...], total: N, limit: 5, offset: 0, hasMore: true}`

---

### `GET /provenance/{uuid}`

Fetch a single record by UUID. Returns `404` if the record does not exist in this workspace.

```bash
curl -s http://localhost:8787/provenance/7b2a8e4f-1c3d-4a5b-8e9f-0d1c2b3a4e5f \
  -H "Authorization: Bearer <access_token>" | jq .
```

Example response (abbreviated):

```json
{
  "uuid": "7b2a8e4f-1c3d-4a5b-8e9f-0d1c2b3a4e5f",
  "record": {
    "workspaceId": "acme-eng",
    "filePath": "src/routes/auth.py",
    "modelName": "claude-opus-4-5",
    "timestampIso": "2026-06-10T14:22:11+00:00",
    "insertedCode": "def rate_limit(request):\n    ...",
    "riskAssessment": {"level": "high", "score": 82},
    "confidenceScore": 0.92,
    "normalizedEvent": { "...full event..." }
  }
}
```

---

### `POST /explain`

Generates a plain-English explanation of a provenance record — what the inserted code does, why it might be risky, and which model authored it.

```bash
curl -s -X POST http://localhost:8787/explain \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspaceId": "acme-eng",
    "uuid": "7b2a8e4f-1c3d-4a5b-8e9f-0d1c2b3a4e5f"
  }' | jq .explanation
```

Pass either `uuid` (fetches the record from the backend) or `record` (the serialized record object directly). On Max, explanations use `EMBEDDING_PROVIDER=openai` if set; otherwise a template fallback is used on all tiers.

---

## Integrity

**Tiers:** Plus · Max only (returns `403` on Lite)

### `GET /integrity/verify`

Walks the hash chain for a workspace and reports the first tampered record. Each record stores a SHA-256 hash of its own content linked to the previous record's hash. Records written before the chain was enabled (e.g. Lite-to-Plus upgrades) are silently skipped.

```bash
curl -s "http://localhost:8787/integrity/verify?workspace_id=acme-eng" \
  -H "Authorization: Bearer <access_token>" | jq .
```

Clean chain response:

```json
{
  "ok": true,
  "records_checked": 1847,
  "first_break_uuid": null,
  "message": "Chain verified across 1847 record(s) — no tampering detected."
}
```

Tampered chain response:

```json
{
  "ok": false,
  "records_checked": 412,
  "first_break_uuid": "9c4d7e2a-...",
  "message": "Hash mismatch at record 9c4d7e2a-... — record may have been tampered with."
}
```

---

### `POST /integrity/aibom`

Generates an HMAC-SHA256 signed AI Bill of Materials for the workspace — percent AI-authored, per-model breakdown, disclosure coverage, and chain status. Use for compliance exports.

```bash
curl -s -X POST \
  "http://localhost:8787/integrity/aibom?workspace_id=acme-eng&date_from=2026-01-01T00:00:00Z&date_to=2026-06-30T23:59:59Z" \
  -H "Authorization: Bearer <access_token>" | jq .
```

Example response (abbreviated):

```json
{
  "workspace_id": "acme-eng",
  "generated_at": "2026-06-10T14:30:00+00:00",
  "summary": {
    "total_records": 1847,
    "percent_ai_authored": 73.4,
    "chain_verified": true,
    "disclosure_coverage": 0.91,
    "models": {
      "claude-opus-4-5": 942,
      "claude-sonnet-4-6": 441,
      "gpt-4o": 464
    }
  },
  "records": [ "...per-record entries..." ],
  "signature": "hmac-sha256:abcd1234..."
}
```

The `signature` field is computed with `HMAC-SHA256(JWT_SECRET_KEY, canonical_json_body)`. Recipients with the same key can verify the document has not been modified.

---

## Export / Agent Trace

**Tiers:** Lite · Plus · Max (admin role required)

### `GET /export/agent-trace`

Exports all provenance records as portable [cursor/agent-trace 0.1.0](https://github.com/cursor/agent-trace) documents. Default format is JSONL (one document per line). Also supports `format=json` (wrapped array) and `format=csv`.

```bash
curl -s "http://localhost:8787/export/agent-trace?format=jsonl&dateFrom=2026-06-01T00:00:00Z" \
  -H "Authorization: Bearer <admin_token>" \
  -o lineagelens-trace.jsonl
```

Optional query params: `dateFrom`, `dateTo`, `toolName` (filter by tool name), `minConfidence` (0.0–1.0).

The output is importable into another LineageLens instance via `POST /import/agent-trace`, or into any tool that speaks the cursor/agent-trace spec.

---

### `POST /import/agent-trace`

Imports a JSONL file produced by `GET /export/agent-trace`. Idempotent — records with a UUID that already exists in the workspace are silently skipped. Max file size: 50 MB / 50,000 lines.

```bash
curl -s -X POST http://localhost:8787/import/agent-trace \
  -H "Authorization: Bearer <admin_token>" \
  -F "file=@lineagelens-trace.jsonl" | jq .
```

```json
{
  "imported": 1243,
  "skipped": 12,
  "errors": [],
  "totalLines": 1255,
  "workspaceId": "acme-eng"
}
```

---

### `GET /export/audit`

Admin-only CSV (default) or JSON export of all provenance records for the workspace. Up to 10,000 records per call. Supports `dateFrom`, `dateTo`, `developer`, `filePath`, and `format=json` query params.

```bash
curl -s "http://localhost:8787/export/audit?format=csv" \
  -H "Authorization: Bearer <admin_token>" \
  -o lineagelens-audit.csv
```

---

## Health

**Tiers:** All (no auth required)

### `GET /health`

```bash
curl -s http://localhost:8787/health | jq .
```

```json
{
  "status": "ok",
  "app": "LineageLens",
  "version": "0.1.0",
  "productMode": "plus",
  "tierLabel": "LineageLens Plus",
  "mcp": true
}
```

When called from `localhost` in a non-production environment, the response also includes `environment`, `backendMode`, and a `features` object listing which subsystems are active. This extended response is intentionally restricted to loopback callers to prevent infrastructure enumeration.

The proxy exposes its own health check at `GET http://localhost:8788/proxy-health` → `{"status": "ok"}`. This is the endpoint the VS Code extension polls every 30 s to detect whether Power Mode is available.
