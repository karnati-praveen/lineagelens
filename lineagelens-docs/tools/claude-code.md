# Claude Code

Capture level: **Full** — prompt, model, edit, applied/rejected status.

Capture method: Proxy (`ANTHROPIC_BASE_URL`). The proxy parses Anthropic `tool_use` / `tool_result` blocks to identify which file-mutating tools (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`) ran and whether each edit was accepted.

---

## Setup

**Easy Mode (no proxy):** Install the VS Code extension. File-level captures are recorded automatically when Claude Code writes 4+ lines to a file you have open. Confidence ~0.35; no prompt or model captured.

**Power Mode (proxy — full capture):**

1. Start the proxy (part of any Lite/Plus/Max quickstart):
   ```bash
   bash lineagelens-scripts/quickstart-lite.sh
   ```

2. Export the env var **in the shell where you run `claude`**:
   ```bash
   export ANTHROPIC_BASE_URL=http://localhost:8788
   ```

3. Run Claude Code normally. Every request is forwarded untouched to Anthropic; the proxy parses the response stream to capture edit records.

To make the setting persistent, add the export to your shell profile (`~/.bashrc`, `~/.zshrc`, or `~/.profile`).

---

## Verification

After completing a Claude Code session that wrote at least one file:

```bash
curl -s http://localhost:8787/provenance?limit=5 \
  -H "Authorization: Bearer <your_token>" | jq '.[0] | {filePath, modelName, captureStatus}'
```

You should see `captureStatus: "full"` and a non-null `modelName`. In the dashboard, the Live Feed tab shows captures in real time.

Alternatively, from Claude Code itself (Plus/Max with MCP server configured):

```
/lineagelens:recent-captures
```

---

## Known limitations

- **CONNECT tunnel (HTTPS_PROXY):** Claude Code uses `ANTHROPIC_BASE_URL` (direct forward proxy), not `HTTPS_PROXY` / `HTTP_PROXY`. The CONNECT tunnel on port 8789 is not needed for Claude Code.
- **Streaming responses:** The proxy reads the full SSE stream before parsing tool blocks, so there is a brief buffering delay on very long responses. The LLM response itself is still streamed to your terminal.
- **Offline / airgapped:** The proxy must be able to reach `api.anthropic.com` (or `UPSTREAM_URL` if overridden). If the proxy can't reach the upstream, the request fails — it does not fall back to a direct connection.
- **Multi-model sessions:** If a session uses multiple models (e.g., via `--model` override), each request is captured with the model reported in that specific Anthropic response.
