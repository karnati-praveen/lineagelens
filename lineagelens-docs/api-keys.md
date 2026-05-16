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

Include the key as a Bearer token in all requests:

```bash
curl http://localhost:8787/provenance \
  -H "Authorization: Bearer llk_<your-key>"
```

Or in the MCP server:
```bash
export LINEAGELENS_ACCESS_TOKEN=llk_<your-key>
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
