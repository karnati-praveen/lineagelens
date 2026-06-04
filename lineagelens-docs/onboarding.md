# LineageLens Onboarding Guide

Welcome to LineageLens! This guide gets you up and running in 15 minutes.

## What is LineageLens?

LineageLens is an AI code provenance platform that tracks every AI-generated code insertion across your team. It captures:
- **What** was generated (the code)
- **By which model** (Claude, GPT-4, etc.)
- **From which prompt** (the conversation context)
- **Who accepted it** (the developer)
- **Risk score** (a heuristic assessment of the insertion)

## Easy Mode vs Power Mode

LineageLens runs in one of two modes. The VS Code status bar item (bottom-right) tells you which is active.

| | Easy Mode | Power Mode |
|---|---|---|
| **Status bar** | `$(zap) LL: Easy` | `$(shield) LL: Power` |
| **Setup required** | None | Start the proxy |
| **Captures** | File path, inserted code, line count, language | + Full prompt messages, model name, applied/rejected status |
| **Confidence** | ~0.35 | 0.8 – 1.0 |
| **Backend needed?** | Optional (local-only by default) | Yes |

**The extension auto-detects the proxy** — when the proxy starts, the status bar switches from `LL: Easy` to `LL: Power` within 30 seconds. No extension restart or reconfiguration needed.

---

## Quick Start (5 minutes)

> **First time?** Start with Option A. The extension runs in Easy Mode immediately after install — no backend, no proxy, no API key. You can upgrade to Power Mode later without reinstalling anything.

### Option A: Easy Mode (zero setup, recommended first run)

1. Install the VS Code extension from the marketplace:
   ```
   code --install-extension karnatipraveen.lineagelens-base
   ```
2. The status bar shows `$(zap) LL: Easy (local)` — you are already capturing. Use any AI coding tool (Copilot, Cursor, Claude Code, etc.) and captures appear in the LineageLens sidebar automatically.
3. **Optional — sync to a backend**: Open VS Code Settings (`Ctrl+,`), search `lineagelens`, and set:
   - `lineagelensBase.backendUrl` → `http://your-backend-host:8787`
   - `lineagelensBase.ingestToken` → your ingest token (from the backend admin panel)
   - `lineagelensBase.workspaceId` → your workspace slug

   The status bar shows `LL: Easy` and captures stream to the dashboard with `capture_status: file_diff`.

### Option B: Power Mode (full prompt + model capture)

1. Your admin will have run the quickstart script. Ask them for:
   - Proxy host/port (default: `http://your-backend-host:8788`)
   - Backend URL (default: `http://your-backend-host:8787`)
   - Your login credentials for the dashboard

2. Add to your shell profile (`.bashrc` / `.zshrc` / `.profile`):
   ```bash
   export ANTHROPIC_BASE_URL=http://your-proxy-host:8788   # Claude Code / Anthropic SDK
   export OPENAI_BASE_URL=http://your-proxy-host:8788       # Codex CLI / Goose
   ```

3. Open a new terminal session. The extension detects the proxy and switches to `LL: Power` in the status bar.

4. Open the dashboard: `http://your-backend-host:8787/dashboard`

---

## Key Concepts

**Provenance Record**: A single capture of an AI code insertion. Each record contains the prompt, the response, the file path, the model, and a risk score.

**Risk Score**: 0-100 heuristic score based on code complexity, security patterns, and model confidence. Scores >= 80 are flagged as high risk.

**Workspace**: Your team's isolated namespace. All records, users, and settings are scoped to a workspace.

**Modes**:
- **Easy Mode** *(default on first install)*: Install extension, captures stored locally (or synced to backend without proxy). `capture_status: file_diff`, confidence ~0.35.
- **Power Mode**: Proxy running, full prompt + model capture. `capture_status: full`, confidence 0.8–1.0. Requires Lite/Plus/Max backend.
- **Lite/Plus/Max**: Backend tiers — SQLite → PostgreSQL → PostgreSQL + Neo4j. All support Power Mode.
- **git-based capture** *(planned)*: Reconstruct AI-assisted commits from `git log` and tool history files without a running extension or proxy. Documented as a future Easy Mode supplement.

---

## Dashboard Features

| Tab | What you'll find |
|---|---|
| Dashboard | Summary cards, risk trend chart, model usage |
| Timeline | AI insertions over time, risk heatmap |
| Graph | File dependency graph (Max mode) |
| Live Feed | Real-time stream of new captures |
| Search | Filter records by model, file, risk, date |
| Developers | Per-developer AI usage breakdown |
| Reviews | Reviewer queue — approve or reject flagged code |
| Team | Member list and invite new members |
| Export | Download audit logs in CSV, JSON, or Parquet |

---

## Reviewer Workflow

1. High-risk records are automatically added to the review queue
2. Reviewers see pending items in the **Reviews** tab
3. Click **Approve** or **Reject** with optional notes
4. Audit log records all review decisions

---

## Admin Tasks

| Task | How |
|---|---|
| Invite a team member | Team tab -> Invite |
| Set RBAC role | Team tab -> click member -> Edit Role |
| Create a policy | Admin -> Policies -> New Policy |
| Configure alerts | Admin -> Alert Channels -> New Channel |
| Export audit log | Export tab -> Audit Export |
| Revoke a user's access | Team tab -> Deactivate |

---

## CLI Reference

```bash
lineagelens start --mode plus   # Start the backend
lineagelens status              # Check health
lineagelens logs --mode plus    # Tail logs
lineagelens stop --mode plus    # Stop the backend
lineagelens upgrade --mode plus # Pull latest and restart
lineagelens config              # Show persistent config
```

---

## Getting Help

- Dashboard status dot in the top bar shows backend health
- `Ctrl+Shift+P` -> "LineageLens: Check Configuration" for diagnostics
- File issues at: https://github.com/karnati-praveen/lineagelens/issues
