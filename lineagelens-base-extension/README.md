# LineageLens

**Local AI code provenance for VS Code.** Captures every multi-line code insertion into your editor — from Copilot, Cursor, Claude, or any AI coding tool — and stores it locally so you can review what was inserted, when, and where.

No backend, no server, no account required by default — just a sidebar showing every AI insertion
in your workspace. Captures stay local unless you opt in to syncing them to a LineageLens backend.

---

## What it captures

When 4+ lines of code appear in your editor in a single change (the signature of an AI insertion or accepted suggestion), LineageLens records:

- The inserted code
- The file path
- The language
- The number of lines added
- The timestamp

You can browse captures in the **LineageLens** sidebar, click any entry to see the full code block, and export the whole history as JSON.

---

## How to use

1. Install the extension.
2. Open the LineageLens icon in the activity bar (left sidebar).
3. Use any AI coding tool (Copilot, Cursor, Claude in VS Code, etc.) and accept a suggestion.
4. The capture appears instantly in the sidebar.

That's it. No setup, no configuration required.

---

## Settings

| Setting | Default | Description |
|---|---|---|
| `lineagelensBase.minInsertionLines` | `4` | Minimum lines to trigger a capture |
| `lineagelensBase.captureEnabled` | `true` | Enable or disable capture |
| `lineagelensBase.maxStoredCaptures` | `1000` | Maximum captures to keep locally |
| `lineagelensBase.excludePatterns` | `node_modules`, `.git`, `dist` | Folders to skip |
| `lineagelensBase.backendUrl` | `""` | Optional LineageLens backend URL (e.g. `http://localhost:8787`). When set, captures are POSTed to the backend (Easy Mode sync). Leave empty for local-only. |
| `lineagelensBase.ingestToken` | `""` | Bearer token for the backend ingest endpoint. Required when `backendUrl` is set. |
| `lineagelensBase.workspaceId` | `vscode-capture` | Workspace slug sent with each ingest payload. |
| `lineagelensBase.proxyHealthUrl` | `http://localhost:8788/proxy-health` | Proxy health URL used to auto-detect Power Mode. |

---

## Commands

- `LineageLens: View Capture Details` — open the detail panel for any capture
- `LineageLens: Export Captures as JSON` — save full history to a file
- `LineageLens: Export Agent Trace` — export captures as portable cursor/agent-trace 0.1.0 JSONL (no backend needed)
- `LineageLens: Clear All Captures` — wipe local storage
- `LineageLens: Refresh` — manually refresh the sidebar
- `LineageLens: Show Mode Status` — show whether you're in Easy or Power Mode
- Right-click a capture for **Insert at Cursor** and **Copy Code**

---

## What it doesn't do

Being honest about scope:

- It doesn't capture the **prompt** you sent to the AI (no proxy, no API interception). This is
  "Easy Mode" — file + lines only. Running the LineageLens proxy ("Power Mode") adds full prompt +
  model capture.
- It can't always tell AI insertions apart from a large manual paste. Any 4+ line single-edit change is captured.
- By default it works locally only. You can optionally sync file-level captures to a LineageLens
  backend by setting `backendUrl` + `ingestToken`, but team dashboards, search, and governance live
  in the backend, not this extension.

For prompt + model capture and team features, see the full LineageLens project.

---

## Privacy

All captures stay on your machine, written to VS Code's global storage folder as plain JSON. Nothing is uploaded anywhere.

---

## Feedback

Issues, suggestions, or bug reports: [github.com/karnati-praveen/lineagelens](https://github.com/karnati-praveen/lineagelens)

---

MIT licensed.
