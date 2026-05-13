# LineageLens

> **Git blame for AI-generated code** — captures every AI insertion and traces it back to its prompt, model, and developer.

LineageLens is an AI code intelligence platform. It sits between your AI coding tools and the AI providers they call, capturing every significant code insertion — what was generated, by which model, from which prompt, and who accepted it. Works with any editor, any AI tool, any team.

---

## How It Works

```
Your AI Tool  →  LineageLens Proxy  →  AI Provider (Anthropic, OpenAI, etc.)
                        ↓
               Provenance Record
     stored in PostgreSQL + optional Neo4j
```

The proxy is transparent. Your AI tool sends requests as normal. LineageLens intercepts the traffic, extracts the prompt and response, and stores a structured provenance record — without modifying any code or slowing down your workflow. The VS Code extension also captures insertions directly via file watchers and Claude Code hook events.

---

## Operating Modes

### LineageLens Base

Zero shared infrastructure. Fully private. Works offline.

- Records stored locally as a JSON file or in VS Code global state.
- Best for individual developers and offline workflows.
- Includes provenance capture, AI-assisted explanation, and local lineage.
- Quickstart: `bash lineagelens-scripts/quickstart-base.sh`
- Deploy: `lineagelens-deploy/docker-compose.base.yml`

### LineageLens Plus

Shared backend without graph complexity.

- FastAPI backend with semantic search, governance dashboard, and team management.
- Runs without Neo4j. pgvector enables vector search when OpenAI embeddings are configured.
- Docker is optional. See [lineagelens-docs/native-backend.md](lineagelens-docs/native-backend.md) to run natively.
- Quickstart: `bash lineagelens-scripts/quickstart-plus.sh`
- Deploy: `lineagelens-deploy/docker-compose.plus.yml`

### LineageLens Max

Complete provenance intelligence and auditability.

- Adds Neo4j graph lineage and full vector search on top of Plus.
- Best for production deployments with compliance requirements.
- Quickstart: `bash lineagelens-scripts/quickstart-max.sh`
- Deploy: `lineagelens-deploy/docker-compose.max.yml`

---

## Quick Start

**Step 1 — Install the extension**

Install `lineagelens-*.vsix` from your release bundle into VS Code.

**Step 2 — Start your backend**
```bash
bash lineagelens-scripts/quickstart-plus.sh    # or quickstart-base.sh / quickstart-max.sh
```

**Step 3 — Point your AI tool at the proxy**

The proxy runs on port `8788`. Set your AI tool's API base URL:
```bash
# Claude Code / Anthropic SDK
export ANTHROPIC_BASE_URL=http://localhost:8788

# OpenAI SDK / any compatible tool
export OPENAI_BASE_URL=http://localhost:8788
```

**Step 4 — Open the dashboard**
```
http://localhost:8787/dashboard
```

**Step 5 — Use the CLI**
```bash
lineagelens start --mode plus    # Start backend (plus or max)
lineagelens status               # Container health
lineagelens logs --mode plus     # Tail logs
lineagelens stop --mode plus     # Stop backend
```

---

## CLI Commands

Install once (requires Node ≥ 18):
```bash
npm install -g .
```

| Command | Description |
|---|---|
| `lineagelens start --mode plus` | Start Plus backend (also: `max`) |
| `lineagelens stop --mode plus` | Stop backend containers |
| `lineagelens status` | Show container health for all modes |
| `lineagelens logs --mode plus` | Tail backend logs (Ctrl+C to stop) |
| `lineagelens logs --mode plus --service backend` | Tail a specific service |

---

## Proxy Setup

LineageLens works with any AI tool that routes HTTP traffic through a configurable base URL.

**Claude Code**
```bash
export ANTHROPIC_BASE_URL=http://localhost:8788
```

**Cursor** — Settings → API → Base URL: `http://localhost:8788`

**Aider**
```bash
aider --openai-api-base http://localhost:8788
```

**Continue**
```json
{ "models": [{ "provider": "anthropic", "apiBase": "http://localhost:8788" }] }
```

**Codeium / Windsurf** — Set `API Server URL` to `http://localhost:8788`

**GitHub Copilot** — Set editor HTTP proxy to `http://localhost:8788`

**All other tools** — Set the API base URL or HTTP proxy to `http://localhost:8788`.

---

## MCP Server

LineageLens ships an MCP server that lets AI assistants query provenance data directly inside the chat — without switching tabs or leaving the conversation.

### Install
```bash
cd lineagelens-mcp
pip install -r lineagelens-mcp-requirements.txt
```

### Configure credentials
```bash
export LINEAGELENS_USERNAME=your-username
export LINEAGELENS_PASSWORD=your-password
export LINEAGELENS_BACKEND_URL=http://localhost:8787
```

Or use a pre-obtained token:
```bash
export LINEAGELENS_ACCESS_TOKEN=your-jwt-token
```

### Wire into Claude Code

Add to `~/.claude/settings.json` (global) or `.claude/settings.json` (project):
```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "python",
      "args": ["/absolute/path/to/lineagelens-mcp/lineagelens-mcp.py"],
      "env": {
        "LINEAGELENS_USERNAME": "your-username",
        "LINEAGELENS_PASSWORD": "your-password",
        "LINEAGELENS_BACKEND_URL": "http://localhost:8787"
      }
    }
  }
}
```

### Available MCP Tools

| Tool | What it does |
|---|---|
| `search_provenance` | Search for AI-generated code by natural language query |
| `get_record` | Full metadata for a specific provenance record by UUID |
| `get_insights` | Governance dashboard — risk scores, compliance, totals |
| `explain_record` | Plain-English explanation of why code was generated |
| `list_recent` | Most recently captured AI insertions |
| `check_file_risk` | Risk breakdown and model usage for a specific file |
| `usage_report` | AI usage summary — lines, models, risk, developers (date-range filterable) |

See [lineagelens-releases/commands.md](lineagelens-releases/commands.md) for complete setup instructions.

---

## Supported AI Tools

LineageLens ships 11 agent adapters that identify which AI tool produced each insertion. The registry runs all adapters in parallel and selects the highest-confidence match.

| Adapter | Tool | Session Kind |
|---|---|---|
| `cursor` | Cursor IDE | `agentic` |
| `copilot` | GitHub Copilot | `assistant` |
| `claude-code` | Claude Code CLI | `cli` / `agentic` |
| `codeium` | Codeium / Windsurf | `agentic` |
| `aider` | Aider | `agentic` |
| `continue` | Continue.dev | `agentic` |
| `cody` | Sourcegraph Cody | `agentic` |
| `amazon-q` | Amazon Q Developer | `assistant` |
| `gemini-cli` | Gemini CLI | `cli` |
| `codex-cli` | OpenAI Codex CLI | `cli` |
| `legacy-heuristic` | Unknown / fallback | `unknown` |

Each match stores: `toolName`, `provider`, `sessionId`, `conversationId`, `runId`, `modelName`, `userAgent`, `operationType`, `confidence`, `evidence[]`, `sessionKind`.

---

## Dashboard

The self-hosted dashboard at `http://localhost:8787/dashboard` includes:

**Governance Overview** — Total records, prompt capture rate, average risk score, high-risk and critical counts, unique files, models, AI lines added, agent sessions, team members. Compliance controls and file hotspots below.

**Timeline** — Chart.js bar + line chart showing AI insertions over time (bars) and average risk trend (line). File risk heatmap below: each file as a horizontal bar, width = relative insertion density, color = risk level (green → red). Click any bar to search that file.

**Graph** — Force-directed canvas graph. Each node is a file; size = insertion count, color = risk. Edges connect files touched by the same AI model. Click a node to search that file.

**Live Feed** — Polls every 30 seconds. New captures animate in with a blue pulse. Badge counter on the tab shows unseen captures. Includes file, risk badge, model, and code snippet per record.

**Search** — Filtered search with keywords, model, file path, and date range. Results open a full record detail modal inline.

**Record Detail Modal** — Click any record from anywhere in the dashboard to open the full provenance modal: inserted code, prompt messages, context snapshot, risk assessment, and an "Explain with AI" button.

**Record Viewer** — Direct UUID lookup tab for manual record inspection.

**Export** — Admin-only CSV audit export with date, developer, and file path filters.

**Team** — Member list with per-user AI insertion counts, lines added, share %, and join date. Admin invite form.

**Theme toggle** — ☀️/🌙 button switches between dark (default) and light mode. Timeline chart re-renders with matching colors.

**Backend status dot** — Green/red indicator in the topbar. Shows backend version and product mode on hover.

---

## Provenance Record Schema

Every stored record is editor-agnostic and tool-agnostic:

- `schemaVersion` — versioned compatibility (`lineagelens.provenance-event.v1`)
- `normalizedEvent` — provider-neutral capture: session, model, file, diff, context, confidence
- `rawData` — original detection payload plus raw proxy request/response fragments
- Adapter-declared capabilities reporting what each integration can provide

Full captures store prompt and response bodies. Metadata-only and tunnel-only captures still preserve routing and session evidence. Opaque tools produce file-diff provenance.

---

## Proxy Capture States

| State | Meaning |
|---|---|
| `full` | Prompt and response body captured |
| `metadata_only` | Only request metadata captured (non-POST) |
| `tunnel_only` | HTTPS CONNECT tunnel recorded; payload not decrypted |
| `unavailable` | Tool not routing through the proxy |

---

## Lightweight Adapter (CLI / Script Ingest)

`lineagelens-src/lightweightRecord.ts` is a pure TypeScript helper with no VS Code dependency. Use it to build provenance records from a CLI, CI job, or any other environment.

```ts
import { buildLightweightProvenanceRecord } from './lineagelens-src/lightweightRecord';

const record = buildLightweightProvenanceRecord({
  eventId: crypto.randomUUID(),
  timestampIso: new Date().toISOString(),
  filePath: 'src/example.ts',
  fileUri: 'file:///workspace/src/example.ts',
  languageId: 'typescript',
  insertedText: 'const answer = 42;',
  promptStatus: 'not-captured',
  captureStatus: 'unavailable'
});
```

See [lineagelens-docs/lightweight-adapters.md](lineagelens-docs/lightweight-adapters.md) for the full contract.

---

## Feature Matrix

| Feature | Base | Plus | Max |
|---|---|---|---|
| Provenance capture | ✓ | ✓ | ✓ |
| WebSocket ingest | ✓ | ✓ | ✓ |
| LLM explain | ✓ | ✓ | ✓ |
| JWT auth + logout | ✓ | ✓ | ✓ |
| Lightweight adapter ingest | ✓ | ✓ | ✓ |
| MCP server | ✓ | ✓ | ✓ |
| Semantic search | — | ✓ | ✓ |
| Governance dashboard | — | ✓ | ✓ |
| Timeline & risk heatmap | — | ✓ | ✓ |
| File lineage graph | — | ✓ | ✓ |
| Live capture feed | — | ✓ | ✓ |
| Team management | — | ✓ | ✓ |
| Audit export (CSV) | — | ✓ | ✓ |
| OpenAI embeddings | — | opt | opt |
| Neo4j graph lineage | — | — | ✓ |
| Vector search | — | — | ✓ |

---

## GitHub Actions Integration

Two workflows included:

**PR Annotation** — Annotates every pull request with which lines are AI-generated, which model produced them, and who accepted them.

**Provenance Review Bot** — Posts a structured AI lineage report as a PR comment, grouping touched blocks by file with risk scores and prompt previews.

See [.github/workflows/](.github/workflows/) for setup.

---

## Build

```bash
npm install
npm run compile
```

Release helpers:
```bash
npm run ship:base
npm run ship:plus
npm run ship:max
```

Backend helpers:
```bash
npm run native:plus
npm run native:max
```

---

## Default Ports

| Service | Port |
|---|---|
| Backend API + Dashboard | 8787 |
| Universal LLM Proxy | 8788 |
| Local extension proxy | 8080 |
| PostgreSQL | 5432 |
| Neo4j Bolt | 7687 |
| Neo4j Browser | 7474 |
