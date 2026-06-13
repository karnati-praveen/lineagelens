# API Key Authentication

LineageLens supports API keys as an alternative to JWT tokens for programmatic access (CI/CD, scripts, integrations).

## Create an API Key

```bash
# Via the dashboard: Admin -> API Keys -> New Key
# Via the API:
curl -X POST http://localhost:8787/api-keys \
  -H "Authorization: Bearer <your-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "CI/CD Key", "scopes": ["read", "write"], "expiresDays": 90}'
```

Response:
```json
{
  "id": "uuid...",
  "name": "CI/CD Key",
  "keyPrefix": "llk_abcd12",
  "key": "llk_<full-key>",
  "scopes": ["read", "write"],
  "expiresAt": "2024-04-01T00:00:00Z",
  "createdAt": "2024-01-01T00:00:00Z"
}
```

> **Save the full key immediately.** It is shown only once at creation time.

## Using API Keys

API keys are sent as the `X-API-Key` request header:

```bash
# CI/CD gate check (the primary use-case for API keys today)
curl -X POST http://localhost:8787/github/check \
  -H "X-API-Key: llk_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{"filePath": "src/auth.py", "code": "..."}'
```

> **Note:** The query endpoints (`/search`, `/provenance`, `/insights/dashboard`, `/explain`)
> currently require a JWT Bearer token. Use `LINEAGELENS_ACCESS_TOKEN` or
> `LINEAGELENS_USERNAME` + `LINEAGELENS_PASSWORD` for those endpoints.

In the MCP server, set `LINEAGELENS_API_KEY` (preferred) or fall back to JWT auth:

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "python",
      "args": ["lineagelens-mcp/lineagelens-mcp.py"],
      "env": {
        "LINEAGELENS_API_KEY": "llk_<your-key>",
        "LINEAGELENS_BACKEND_URL": "http://localhost:8787"
      }
    }
  }
}
```

For JWT-authenticated endpoints (search, insights, explain), use a dedicated read-only
member account rather than an admin account:

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "python",
      "args": ["lineagelens-mcp/lineagelens-mcp.py"],
      "env": {
        "LINEAGELENS_USERNAME": "mcp-readonly",
        "LINEAGELENS_PASSWORD": "<password>",
        "LINEAGELENS_BACKEND_URL": "http://localhost:8787"
      }
    }
  }
}
```

## Scopes

| Scope | Access |
|---|---|
| `read` | GET endpoints — search, view records, analytics |
| `write` | POST endpoints — ingest, tag, comment |
| `admin` | All admin endpoints — delete, export, manage team |

## Revoking Keys

```bash
# Via dashboard: Admin -> API Keys -> Revoke
# Via API:
curl -X DELETE http://localhost:8787/api-keys/<key-id> \
  -H "Authorization: Bearer <your-jwt-token>"
```

Revoked keys return `401 Unauthorized` immediately.

## Best Practices

- Use short expiry for CI/CD keys (30-90 days)
- Use `read`-only scopes for monitoring/analytics integrations
- Rotate keys regularly
- Never commit keys to source control — use environment variables or secrets managers
