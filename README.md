# LineageLens

LineageLens is a provenance system for AI-generated code. It captures every significant code insertion made by any AI coding tool — Claude Code, Cursor, Aider, GitHub Copilot, Codeium, Windsurf, Continue, Sourcegraph Cody, Amazon Q, Gemini CLI, OpenAI Codex — and links each insertion to its prompt, model, session, and context. VS Code is the current observation host; the provenance record schema and backend are tool-agnostic.

## Operating Modes

### LineageLens Base

Zero setup. Fully private. Works offline.

- No backend required. Data is stored locally in VS Code storage or an optional workspace file.
- Best for individual and offline workflows.
- Start from Command Palette (`Ctrl+Shift+P`) → `Start LineageLens`.

### LineageLens Plus

Shared backend without graph complexity.

- Connects to the FastAPI backend for shared ingest, auth, semantic search, and the governance dashboard.
- Runs without Neo4j or vector search.
- Docker is optional. See [docs/native-backend.md](docs/native-backend.md) to run the backend natively.
- Deploy: `deploy/docker-compose.plus.yml` + `deploy/.env.plus.example`.
- Quickstart: `bash scripts/quickstart-plus.sh`

### LineageLens Max

Complete provenance intelligence and auditability.

- Adds Neo4j graph lineage and vector search on top of Plus.
- Best for production deployments with compliance requirements.
- Docker is optional. See [docs/native-backend.md](docs/native-backend.md).
- Deploy: `deploy/docker-compose.max.yml` + `deploy/.env.max.example`.
- Quickstart: `bash scripts/quickstart-max.sh`

See [docs/lightweight-adapters.md](docs/lightweight-adapters.md) for the lightweight adapter contract (CLI/script ingest without VS Code).
See [docs/shipping-modes.md](docs/shipping-modes.md) for the release layout.
See [docs/SHIP_PRODUCTS_COMMANDS.md](docs/SHIP_PRODUCTS_COMMANDS.md) for exact ship commands.

## Switch from Local to Backend Mode

1. Start your backend service (default: `http://127.0.0.1:8787`).
2. Open Command Palette (`Ctrl+Shift+P`).
3. Run `AI Provenance: Switch to Backend Mode`.
4. Run `AI Insertion Detector: Backend Login` and authenticate.
5. Optionally set backend endpoints in Settings if different from defaults.

### Settings JSON Example

```json
{
  "aiCodeProvenance.mode": "backend",
  "aiInsertionDetector.backend.baseUrl": "http://127.0.0.1:8787",
  "aiInsertionDetector.backend.websocketUrl": "ws://127.0.0.1:8787/ws/capture"
}
```

## Commands

| Command | Description |
|---|---|
| `Start LineageLens` | Activate capture and proxy |
| `AI Insertion Detector: Toggle Feature` | Enable / disable capture |
| `AI Insertion Detector: Show Status` | View proxy capability report |
| `AI Insertion Detector: Show Provenance` | Open provenance sidebar |
| `AI Provenance: Show Adapter Diagnostics` | Inspect adapter match, evidence, confidence |
| `AI Insertion Detector: Open Provenance Search` | Open search panel (Plus/Max) |
| `AI Insertion Detector: Backend Login` | Authenticate to backend |

## Agent Adapters

LineageLens ships with 10 agent adapters that identify which AI tool produced each insertion. The registry runs all adapters and selects the highest-confidence match.

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

Each match stores normalized session details:

- `toolName`, `provider`, `sessionId`, `conversationId`, `runId`
- `modelName`, `userAgent`, `workspaceHint`, `operationType`
- `confidence` and `evidence[]` used for the match
- `sessionKind` (`cli`, `agentic`, `assistant`, `unknown`)

When no adapter matches confidently, the legacy heuristic adapter fires as a fallback.

## Provider-Agnostic Provenance Core

The VS Code extension is a thin observation shim. The underlying provenance contract works the same regardless of which tool wrote the code. Each stored record includes:

- `schemaVersion` — versioned compatibility (`lineagelens.provenance-event.v1`)
- `normalizedEvent` — IDE/provider-neutral capture: session, model, file, diff, context, confidence
- `rawData` — original detection payload plus raw proxy request/response fragments where available
- Adapter-declared capabilities so integrations report what they can provide

This keeps all 10 supported tools — plus opaque HTTPS tunnels and future IDE agents — on the same record shape. Full captures store prompt/response bodies; metadata-only and tunnel-only captures still preserve routing and session evidence; completely opaque tools produce file-diff provenance.

## Proxy Capture States

The local proxy reports capture state as one of:

| State | Meaning |
|---|---|
| `full` | Prompt and response body captured |
| `metadata_only` | Only request metadata captured (non-POST) |
| `tunnel_only` | HTTPS CONNECT tunnel recorded; payload not decrypted |
| `unavailable` | Tool not routing through the proxy |

Use `AI Insertion Detector: Show Status` to view the proxy capability report.

## Adapter Diagnostics

Use `AI Provenance: Show Adapter Diagnostics` to inspect:

- Which adapter matched a record and at what confidence
- The evidence signals used for the match
- The capture status and correlation confidence
- The fallback metadata when a record was only heuristically grouped

## Lightweight Adapter (CLI / Script Ingest)

`src/lightweightRecord.ts` is a pure TypeScript helper (no VS Code dependency) for building provenance records from a CLI, script, CI job, or another editor. It accepts a minimal payload and produces the same `ProviderAgnosticProvenanceEvent` the extension uses.

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

## Build and Package

```bash
npm install
npm run compile
npm run package:vsix
```

Mode-specific release helpers:

```bash
npm run ship:base
npm run ship:plus
npm run ship:max
```

Native backend helpers:

```bash
npm run native:plus
npm run native:max
npm run native:test
```
