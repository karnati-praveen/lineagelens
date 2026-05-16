# LineageLens Onboarding Guide

Welcome to LineageLens! This guide gets you up and running in 15 minutes.

## What is LineageLens?

LineageLens is an AI code provenance platform that tracks every AI-generated code insertion across your team. It captures:
- **What** was generated (the code)
- **By which model** (Claude, GPT-4, etc.)
- **From which prompt** (the conversation context)
- **Who accepted it** (the developer)
- **Risk score** (a heuristic assessment of the insertion)

## Quick Start (5 minutes)

### 1. Install the VS Code Extension

Your admin will provide a `.vsix` file. Install it:
```
Extensions sidebar -> "..." menu -> Install from VSIX
```

### 2. Configure the Backend URL

Open VS Code settings (`Ctrl+,`) and search for `LineageLens`. Set:
- `lineagelens.backendUrl`: `http://your-backend-host:8787`

Or use the setup wizard: `Ctrl+Shift+P` -> "LineageLens: Run Setup Wizard"

### 3. Point Your AI Tool at the Proxy

The proxy at port 8788 captures all AI API traffic automatically.

```bash
# Add to your shell profile (.bashrc / .zshrc / .profile):
export ANTHROPIC_BASE_URL=http://your-proxy-host:8788
export OPENAI_BASE_URL=http://your-proxy-host:8788
```

### 4. Open the Dashboard

Navigate to `http://your-backend-host:8787/dashboard` and log in with your team credentials.

---

## Key Concepts

**Provenance Record**: A single capture of an AI code insertion. Each record contains the prompt, the response, the file path, the model, and a risk score.

**Risk Score**: 0-100 heuristic score based on code complexity, security patterns, and model confidence. Scores >= 80 are flagged as high risk.

**Workspace**: Your team's isolated namespace. All records, users, and settings are scoped to a workspace.

**Modes**:
- **Base**: Local-only storage. No server required.
- **Plus**: Shared PostgreSQL backend with dashboard and team features.
- **Max**: Plus Neo4j graph lineage and vector search.

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
- File issues at: https://github.com/lineagelens/lineagelens/issues
