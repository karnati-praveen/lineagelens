# Gemini CLI

Capture level: **Full** — prompt, model, edit, applied/rejected status.

Capture method: Proxy. The proxy detects Gemini requests by path (`/v1beta/`, `/v1/models/<name>:generateContent`) and parses `functionCall` / `functionResponse` parts to identify file edits and their outcomes.

---

## Setup

**Easy Mode (no proxy):** Install the VS Code extension. File-level captures are recorded when Gemini writes 4+ lines. Confidence ~0.35; no prompt or model captured.

**Power Mode (proxy — full capture):**

1. Start the proxy:
   ```bash
   bash lineagelens-scripts/quickstart-lite.sh
   ```

2. Point Gemini CLI at the proxy. The exact env var depends on which Gemini CLI you use:

   ```bash
   # google/generative-ai based CLIs and SDKs
   export GOOGLE_API_BASE_URL=http://localhost:8788

   # If the CLI reads GEMINI_UPSTREAM_URL or a similar var, set that instead.
   # The proxy also accepts Gemini traffic when the path starts with /v1beta/.
   ```

   If the CLI does not support a configurable base URL, set `HTTPS_PROXY=http://localhost:8789` to route through the CONNECT tunnel on port 8789 instead.

3. Run Gemini CLI normally.

---

## Verification

After a Gemini session that modified files:

```bash
curl -s http://localhost:8787/provenance?limit=5 \
  -H "Authorization: Bearer <your_token>" | jq '.[0] | {filePath, modelName, captureStatus}'
```

Expect `modelName` to contain the Gemini model ID (e.g., `"gemini-2.0-flash"`) and `captureStatus: "full"`.

---

## Known limitations

- **Path-based detection:** The proxy identifies Gemini traffic by URL path pattern. If Google changes the API path structure, the detection heuristic may need updating.
- **`functionCall` only:** Only function-call-based file edits are captured. If Gemini CLI produces code in plain text (not a function call) and writes it to disk via its own mechanism, the proxy cannot correlate that edit. The extension captures the file write in that case, but at `captureStatus: file_diff` (confidence ~0.35).
- **`HTTPS_PROXY` tunnel (port 8789):** The CONNECT tunnel does not perform TLS interception by default. To capture the full request body through the tunnel you need to configure `PROXY_CA_CERT_PATH` and `PROXY_CA_KEY_PATH` with a CA cert the Gemini CLI trusts. Without these, CONNECT gives you tunnel-only capture (metadata, not prompt content).
