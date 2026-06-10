# Continue

Capture level: **Full or editor-only**, depending on the provider configured in Continue.

- **Full capture** (prompt, model, edit, applied/rejected): when Continue is configured to use a provider that speaks a native tool-calling protocol that the proxy understands — Anthropic (`tool_use`) or OpenAI (`function_call`).
- **Editor-only capture** (file + lines, confidence ~0.35): when Continue uses a text-based completion or a provider the proxy does not parse for tool calls (e.g., Ollama, plain `/chat/completions` without function calling).

Capture method: Proxy (for full capture) or VS Code extension (for editor-only).

---

## Setup — Full capture via proxy

Use this path when Continue is configured with an Anthropic or OpenAI provider.

1. Start the proxy:
   ```bash
   bash lineagelens-scripts/quickstart-lite.sh
   ```

2. In Continue's `~/.continue/config.json`, set the provider's `apiBase` to the proxy:

   ```json
   {
     "models": [
       {
         "title": "Claude via LineageLens proxy",
         "provider": "anthropic",
         "model": "claude-opus-4-5",
         "apiBase": "http://localhost:8788"
       }
     ]
   }
   ```

   For OpenAI:
   ```json
   {
     "models": [
       {
         "title": "GPT-4o via LineageLens proxy",
         "provider": "openai",
         "model": "gpt-4o",
         "apiBase": "http://localhost:8788"
       }
     ]
   }
   ```

3. Reload the Continue extension (or restart VS Code). New requests route through the proxy.

---

## Setup — Editor-only capture (fallback)

No proxy configuration needed. Install the LineageLens Base extension:

```
code --install-extension karnatipraveen.lineagelens-base
```

The extension captures any insertion of 4+ lines regardless of which AI backend Continue is using. Confidence is ~0.35 and prompt/model are not captured.

---

## What gets captured

| Field | Full capture | Editor-only |
|---|---|---|
| File path | Yes | Yes |
| Inserted lines | Yes | Yes |
| Prompt | Yes | No |
| Model | Yes | No |
| Applied/rejected | Yes | No |
| Confidence | 0.8–1.0 | ~0.35 |

---

## Verification

After a Continue session that wrote at least one file:

```bash
curl -s "http://localhost:8787/provenance?limit=5" \
  -H "Authorization: Bearer <your_token>" | jq '.[0] | {filePath, modelName, captureStatus}'
```

For full capture, expect `captureStatus: "full"`. For editor-only, expect `captureStatus: "file_diff"`.

---

## Known limitations

- **Text-fallback path:** Continue supports providers that do not use function calling (plain completion, Ollama, LM Studio). The proxy cannot extract structured edit records from these responses; captures fall back to editor-level only.
- **Provider must set `apiBase`:** The env var approach (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`) does not affect Continue because Continue manages its own HTTP client. The `apiBase` field in `config.json` is required.
- **Multiple models in one session:** If a Continue session switches between a proxy-routed model and a local model (e.g., Ollama), captures for the local model are editor-only even if the proxy captures captures for the remote model correctly.
