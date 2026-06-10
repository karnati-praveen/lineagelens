# OpenAI Codex CLI

Capture level: **Full** — prompt, model, edit, applied/rejected status.

Capture method: Proxy (`OPENAI_BASE_URL`). The proxy parses the OpenAI Responses API `function_call` items and the `apply_patch` DSL to identify file edits, then resolves each edit's applied/rejected status from the subsequent `tool_result`.

---

## Setup

**Easy Mode (no proxy):** Install the VS Code extension. File-level captures are recorded when Codex writes 4+ lines. Confidence ~0.35; no prompt or model captured.

**Power Mode (proxy — full capture):**

1. Start the proxy:
   ```bash
   bash lineagelens-scripts/quickstart-lite.sh
   ```

2. Export the env var in the shell where you run `codex`:
   ```bash
   export OPENAI_BASE_URL=http://localhost:8788
   ```

3. Run Codex normally. Requests are forwarded to `api.openai.com` (or `OPENAI_UPSTREAM_URL` if set).

For tools that use the OpenAI SDK with a custom base URL (e.g., Goose configured for OpenAI), the same `OPENAI_BASE_URL` env var applies.

---

## Verification

After a Codex session that wrote at least one file:

```bash
curl -s http://localhost:8787/provenance?limit=5 \
  -H "Authorization: Bearer <your_token>" | jq '.[0] | {filePath, modelName, captureStatus}'
```

Expect `captureStatus: "full"` and a non-null `modelName` (e.g., `"gpt-4o"`).

---

## Known limitations

- **Responses API only:** The proxy parses the Responses API (`/v1/responses`) and chat completions (`/v1/chat/completions`). Codex CLI uses the Responses API; older OpenAI tools that use completions or `/v1/edits` are not parsed for file edits.
- **`apply_patch` DSL:** The proxy extracts file paths from `apply_patch` tool calls. If Codex uses a different file-mutation mechanism in a future version, the edit records may degrade to metadata-only.
- **Azure OpenAI endpoints:** The proxy can forward to Azure OpenAI (`*.openai.azure.com`). Set `OPENAI_UPSTREAM_URL=https://your-instance.openai.azure.com` in the proxy environment. Azure-specific auth headers are passed through untouched.
