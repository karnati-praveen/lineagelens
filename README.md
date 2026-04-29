# LineageLens

> **Git blame for AI-generated code** — captures every AI insertion and traces it back to its prompt, model, and developer.

LineageLens is an AI code intelligence platform. It sits between your AI coding tools and the AI providers they call, capturing every significant code insertion — what was generated, by which model, from which prompt, and who accepted it. Works with any editor, any AI tool, any team.

---

## How It Works

```
Your AI Tool  →  LineageLens Proxy  →  AI Provider (Anthropic, OpenAI, etc.)
                        ↓
               Provenance Record
          stored locally or sent to backend
```

The proxy is transparent. Your AI tool sends requests as normal. LineageLens intercepts the traffic, extracts the prompt and response, and stores a structured provenance record — without modifying any code or slowing down your workflow.

---

## Operating Modes

### LineageLens Base

Zero setup. Fully private. Works offline.

- No backend required. Records stored locally as a JSON file.
- Best for individual developers and offline workflows.
- Start the proxy and begin capturing immediately.

### LineageLens Plus

Shared backend without graph complexity.

- Connects to the FastAPI backend for shared ingest, auth, semantic search, and the governance dashboard.
- Runs without Neo4j or vector search.
- Docker is optional. See [docs/native-backend.md](docs/native-backend.md) to run the backend natively.
- Deploy: `deploy/docker-compose.plus.yml` + `deploy/.env.plus.example`
- Quickstart: `bash scripts/quickstart-plus.sh`

### LineageLens Max

Complete provenance intelligence and auditability.

- Adds Neo4j graph lineage and vector search on top of Plus.
- Best for production deployments with compliance requirements.
- Docker is optional. See [docs/native-backend.md](docs/native-backend.md).
- Deploy: `deploy/docker-compose.max.yml` + `deploy/.env.max.example`
- Quickstart: `bash scripts/quickstart-max.sh`

See [docs/lightweight-adapters.md](docs/lightweight-adapters.md) for the lightweight adapter contract (CLI/script ingest).
See [docs/shipping-modes.md](docs/shipping-modes.md) for the release layout.

---

## Quick Start

**Step 1 — Install the proxy**
```bash
npm install -g @lineagelens/proxy
```

**Step 2 — Start the proxy**
```bash
lineagelens-proxy start --port 7777
```

**Step 3 — Point your AI tool at the proxy**

Set your AI tool's API base URL to `http://localhost:7777`. See [Proxy Setup](#proxy-setup) below for per-tool instructions.

**Step 4 — Trace any line**
```bash
lineagelens trace src/utils/parser.ts 42
```

Or open the web dashboard at `http://localhost:8000/dashboard`.

---

## Switch from Local to Backend Mode

1. Start your backend service (default: `http://127.0.0.1:8787`).
2. Set these values in your `lineagelens.config.json` (or pass as CLI flags):

```json
{
  "mode": "backend",
  "backendUrl": "http://127.0.0.1:8787",
  "websocketUrl": "ws://127.0.0.1:8787/ws/capture"
}
```

3. Authenticate:
```bash
lineagelens login --backend http://127.0.0.1:8787
```

---

## Proxy Setup

LineageLens works with any AI tool that routes HTTP/HTTPS traffic through a configurable base URL.

**Cursor**
```
API Base URL: http://localhost:7777
```

**Claude Code**
```bash
export ANTHROPIC_BASE_URL=http://localhost:7777
```

**GitHub Copilot**
Set your editor's HTTP proxy to `http://localhost:7777`.

**Aider**
```bash
aider --openai-api-base http://localhost:7777
```

**Codeium / Windsurf**
Set `API Server URL` to `http://localhost:7777` in Codeium settings.

**Continue**
```json
{
  "models": [{ "provider": "anthropic", "apiBase": "http://localhost:7777" }]
}
```

**All other tools** — set the API base URL or HTTP proxy to `http://localhost:7777`.

---

## Supported AI Tools

LineageLens ships 10 agent adapters that identify which AI tool produced each insertion. The registry runs all adapters in parallel and selects the highest-confidence match.

| Adapter | Tool | Session Kind |
|---|---|---|
| `cursor` | Cursor | `agentic` |
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

Each match stores:

- `toolName`, `provider`, `sessionId`, `conversationId`, `runId`
- `modelName`, `userAgent`, `workspaceHint`, `operationType`
- `confidence` and `evidence[]` used for the match
- `sessionKind` (`cli`, `agentic`, `assistant`, `unknown`)

When no adapter matches confidently, the legacy heuristic adapter fires as a fallback.

---

## Provenance Record Schema

Every stored record is editor-agnostic and tool-agnostic:

- `schemaVersion` — versioned compatibility (`lineagelens.provenance-event.v1`)
- `normalizedEvent` — provider-neutral capture: session, model, file, diff, context, confidence
- `rawData` — original detection payload plus raw proxy request/response fragments where available
- Adapter-declared capabilities so integrations report what they can provide

Full captures store prompt and response bodies. Metadata-only and tunnel-only captures still preserve routing and session evidence. Completely opaque tools produce file-diff provenance.

---

## Proxy Capture States

| State | Meaning |
|---|---|
| `full` | Prompt and response body captured |
| `metadata_only` | Only request metadata captured (non-POST) |
| `tunnel_only` | HTTPS CONNECT tunnel recorded; payload not decrypted |
| `unavailable` | Tool not routing through the proxy |

Check capture state:
```bash
lineagelens status
```

---

## Adapter Diagnostics

Inspect why a specific insertion was attributed to a particular tool:

```bash
lineagelens diagnose <uuid>
```

Shows:
- Which adapter matched and at what confidence
- The evidence signals used for the match
- The capture status and correlation confidence
- The fallback metadata when a record was only heuristically grouped

---

## Lightweight Adapter (CLI / Script Ingest)

`src/lightweightRecord.ts` is a pure TypeScript helper with no editor dependency. Use it to build provenance records from a CLI, script, CI job, or any other environment.

```ts
import { buildLightweightProvenanceRecord } from './src/lightweightRecord';

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

See [docs/lightweight-adapters.md](docs/lightweight-adapters.md) for the full contract and optional fields.

---

## Feature Matrix

| Feature | Base | Plus | Max |
|---|---|---|---|
| Provenance capture | yes | yes | yes |
| WebSocket ingest | yes | yes | yes |
| LLM explain | yes | yes | yes |
| JWT auth | yes | yes | yes |
| Lightweight adapter ingest | yes | yes | yes |
| Semantic search | no | yes | yes |
| Governance dashboard | no | yes | yes |
| Team management | no | yes | yes |
| Neo4j graph lineage | no | no | yes |
| Vector search | no | no | yes |

---

## GitHub Actions Integration

LineageLens includes two GitHub Actions workflows:

**PR Annotation** — automatically annotates every pull request with which lines are AI-generated, which model produced them, and who accepted them.

**Provenance Review Bot** — posts a structured AI lineage report as a PR comment, grouping touched blocks by file with risk scores and prompt previews.

See [.github/workflows/](.github/workflows/) for setup instructions.

---

## Build

```bash
npm install
npm run compile
```

Mode-specific release helpers:

```bash
npm run ship:base
npm run ship:plus
npm run ship:max
```

Backend helpers:

```bash
npm run native:plus
npm run native:max
npm run native:test
```
