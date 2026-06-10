# Dynamic Model Routing

The LineageLens proxy automatically routes each request to the most
cost-appropriate model for that provider.  It works for **all three CLI tools**
simultaneously — Claude Code, Codex CLI, and Gemini CLI — each routed to its own
upstream with no configuration required on the client side.

## How it works

1. Every inbound request is classified as `simple`, `standard`, or `complex`
   using deterministic rules (no ML).
2. The proxy looks up the **RoutingPolicy** for this workspace + detected provider.
3. If a policy is enabled, the `model` field is overwritten before forwarding.
4. The original model, routed model, tier, and savings estimate are stored in
   `provenance_records.routing_decision` for every routed request.
5. The dashboard **"AI Cost Saved by Routing (30d)"** card shows the total.

## Multi-CLI / multi-provider support

All three tools can point at the same proxy URL.  The proxy detects each
provider from the inbound path and headers and forwards to the matching upstream.

| CLI tool | Inbound signal | Upstream env var |
|----------|---------------|-----------------|
| Claude Code CLI | `/v1/messages` + `anthropic-version` header | `ANTHROPIC_UPSTREAM_URL` |
| Codex CLI | `/v1/chat/completions` or `/v1/responses` | `OPENAI_UPSTREAM_URL` |
| Gemini CLI | `/v1beta/…:generateContent` | `GEMINI_UPSTREAM_URL` |

Set all three in the proxy's environment (or leave empty to fall back to
`UPSTREAM_URL`):

```env
ANTHROPIC_UPSTREAM_URL=https://api.anthropic.com
OPENAI_UPSTREAM_URL=https://api.openai.com
GEMINI_UPSTREAM_URL=https://generativelanguage.googleapis.com
```

## Classifier rules (priority order)

| Priority | Condition | Tier |
|----------|-----------|------|
| 1 | `tools` / `functions` array is non-empty | `complex` |
| 2 | Approximate prompt tokens > 8 000 (chars / 4) | `complex` |
| 3 | System message contains `refactor`, `design`, `architect`, `security`, `vulnerability`, or `audit` | `complex` |
| 4 | Any code fence in the prompt has > 100 lines | `standard` |
| 5 | Last user message < 200 chars and contains no code fence | `simple` |
| 6 | Default | `standard` |

## Built-in default model mappings

Each provider has sensible defaults so you never need to look up model names.
Supply `"mappings": {}` (or omit it) to use these; supply explicit names to override.

| Provider | simple | standard | complex |
|----------|--------|----------|---------|
| `anthropic` | `claude-haiku-4-5-20251001` | `claude-sonnet-4-6` | `claude-opus-4-7` |
| `openai` | `gpt-4o-mini` | `gpt-4o-mini` | `gpt-4o` |
| `gemini` | `gemini-2.5-flash` | `gemini-2.5-flash` | `gemini-2.5-pro` |

Fetch the current defaults at any time:

```bash
curl https://your-backend/policies/routing/defaults \
  -H "Authorization: Bearer <admin-jwt>"
```

## Configuration

### Enable routing (minimal — uses built-in defaults)

```bash
# One call per provider your team uses.  Omit "mappings" to accept defaults.
for PROVIDER in anthropic openai gemini; do
  curl -X PUT https://your-backend/policies/routing \
    -H "Authorization: Bearer <admin-jwt>" \
    -H "Content-Type: application/json" \
    -d "{\"workspaceId\":\"your-ws\",\"provider\":\"$PROVIDER\",\"enabled\":true}"
done
```

### Override specific model names (optional)

```bash
curl -X PUT https://your-backend/policies/routing \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "workspaceId": "your-ws",
    "provider": "openai",
    "mappings": {
      "simple":   "gpt-4o-mini",
      "standard": "gpt-4o",
      "complex":  "gpt-4o"
    },
    "enabled": true
  }'
```

### View / disable

```bash
# View all policies
curl https://your-backend/policies/routing -H "Authorization: Bearer <admin-jwt>"

# Disable for one provider (keeps mappings intact)
curl -X PUT https://your-backend/policies/routing \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"workspaceId":"your-ws","provider":"openai","enabled":false}'
```

## V1 limitations

> **⚠️ V1 does NOT route across providers.**
> An Anthropic request routes only to another Anthropic model;
> it never redirects to OpenAI or Gemini, and vice-versa.

- Classification is purely deterministic — no ML.
- No fallback if the routed model returns an error.
- Policy edits take up to 60 seconds to propagate to the proxy cache.

## Running the tests

```bash
cd lineagelens-proxy
pytest test_classifier.py test_routing.py test_routing_integration.py test_provider_routing.py -v

cd lineagelens-backend
pytest tests/test_routing_policy.py -v
```
