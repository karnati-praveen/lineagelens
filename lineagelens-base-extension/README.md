# LineageLens

**Local AI code provenance for VS Code.** Captures every multi-line code insertion into your editor — from Copilot, Cursor, Claude, or any AI coding tool — and stores it locally so you can review what was inserted, when, and where.

No backend. No server. No cloud. Just a sidebar showing every AI insertion in your workspace.

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

---

## Commands

- `LineageLens: View Capture Details` — open the detail panel for any capture
- `LineageLens: Export Captures as JSON` — save full history to a file
- `LineageLens: Clear All Captures` — wipe local storage
- `LineageLens: Refresh` — manually refresh the sidebar

---

## What it doesn't do

Being honest about scope:

- It doesn't capture the **prompt** you sent to the AI (no proxy, no API interception).
- It can't always tell AI insertions apart from a large manual paste. Any 4+ line single-edit change is captured.
- It works locally only — no team sharing, no dashboard, no governance.

For prompt + model capture and team features, see the full LineageLens project.

---

## Privacy

All captures stay on your machine, written to VS Code's global storage folder as plain JSON. Nothing is uploaded anywhere.

---

## Feedback

Issues, suggestions, or bug reports: [github.com/karnati-praveen/lineagelens](https://github.com/karnati-praveen/lineagelens)

---

MIT licensed.
