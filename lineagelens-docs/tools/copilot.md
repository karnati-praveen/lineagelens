# GitHub Copilot

Capture level: **Editor-only** — file path, inserted lines, language, confidence. No prompt or model.

Capture method: VS Code extension (file watcher). Copilot's traffic goes to `api.githubcopilot.com`, a proprietary backend. The LineageLens proxy cannot intercept it.

---

## Setup

Install the LineageLens Base extension. It is compatible with VS Code and any VS Code fork that runs standard extensions.

```
code --install-extension karnatipraveen.lineagelens-base
```

The status bar shows `LL: Easy (local)` immediately. Use Copilot as normal; insertions of 4+ lines appear in the LineageLens sidebar automatically.

**Optional — sync to a backend:**

Open VS Code Settings (`Ctrl+,`) and set:

- `lineagelensBase.backendUrl` → `http://your-backend-host:8787`
- `lineagelensBase.ingestToken` → your ingest token
- `lineagelensBase.workspaceId` → your workspace slug

---

## What gets captured

| Field | Value |
|---|---|
| File path | Yes |
| Language | Yes |
| Inserted lines (count) | Yes |
| Inserted code (preview) | Yes (4+ line threshold) |
| Prompt / conversation | No |
| Model | No |
| Applied / rejected status | No |
| Confidence | ~0.35 |

---

## Verification

Accept a Copilot suggestion that inserts 4 or more lines. The LineageLens sidebar should show a new capture within seconds. If syncing to a backend:

```bash
curl -s "http://localhost:8787/provenance?limit=5" \
  -H "Authorization: Bearer <your_token>" | jq '.[0] | {filePath, captureStatus}'
```

The record will have `captureStatus: file_diff` and `source.toolName: "copilot"` (or the generic `unknown` if the heuristic does not match Copilot's signature in this session).

---

## Known limitations

- **No prompt or model capture.** Copilot routes all traffic — inline suggestions, Chat, and Workspace agent — through `api.githubcopilot.com`. This is not a configurable base URL and cannot be intercepted by the proxy.
- **GitHub Copilot CLI is not supported.** The CLI (`gh copilot`) also uses a proprietary endpoint and is not captured at any level.
- **Inline suggestions vs Chat.** The extension cannot distinguish between an inline Tab completion and a Copilot Chat suggestion — both appear as `file_diff` captures at confidence ~0.35.
- **Windsurf (Codeium)** has the same limitation. Its agent traffic goes to a proprietary backend; only editor-level captures are available.
