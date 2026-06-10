# Cursor

Capture level: **Editor-only** — file path, inserted lines, language, confidence. No prompt or model.

Capture method: VS Code extension (file watcher). Cursor's agent traffic goes to `api.cursor.sh`, a proprietary backend that does not expose a configurable base URL. The LineageLens proxy cannot intercept it.

---

## Setup

Install the VS Code-compatible extension. Cursor is based on VS Code and runs VS Code extensions natively.

```
code --install-extension karnatipraveen.lineagelens-base
```

The status bar shows `LL: Easy (local)` immediately after install. No configuration required.

**Optional — sync to a backend:**

Open Cursor Settings (`Ctrl+,`) and set:

- `lineagelensBase.backendUrl` → `http://your-backend-host:8787`
- `lineagelensBase.ingestToken` → your ingest token (from the backend admin panel)
- `lineagelensBase.workspaceId` → your workspace slug

Captures then stream to the backend dashboard as `capture_status: file_diff` records.

---

## What gets captured

| Field | Value |
|---|---|
| File path | Yes |
| Language | Yes |
| Inserted lines (count) | Yes |
| Inserted code (preview) | Yes (4+ line threshold) |
| Prompt | No |
| Model | No |
| Applied / rejected status | No |
| Confidence | ~0.35 |

---

## Verification

In Cursor, make any AI-assisted edit (Cmd+K, Composer, or inline suggestion) that inserts 4 or more lines. Within a few seconds, the LineageLens sidebar should show a new capture. The record will have `captureStatus: file_diff`.

If syncing to a backend, run:

```bash
curl -s "http://localhost:8787/provenance?limit=5" \
  -H "Authorization: Bearer <your_token>" | jq '.[0] | {filePath, captureStatus}'
```

---

## Known limitations

- **No prompt or model capture.** Cursor routes agent conversations through its own proprietary API (`api.cursor.sh`). There is no supported way to intercept this traffic for prompt or model attribution.
- **Confidence ~0.35.** The extension uses the file watcher path (no proxy correlation), so the five-signal confidence engine scores these records at approximately 0.35 — meaning attribution is to "a Cursor edit", not to a specific prompt.
- **Tab completions vs agent edits.** The extension captures any insertion of 4+ lines, whether from Cursor Tab (autocomplete), Composer, or Cmd+K. It cannot distinguish between them at this capture level.
