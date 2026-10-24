# Change Log

## [Unreleased] - 2026-10-24

### Added
- **Llama support, pinned by tests** — Llama served over any OpenAI-compatible endpoint (Llama API,
  vLLM, llama.cpp, Ollama, Together, Groq) is captured with full fidelity through the proxy's
  chat-completions adapter. `lineagelens-proxy/test_llama_conformance.py` pins the contract:
  tool-call write/replace extraction, `meta-llama/…` model attribution into the captured prompt
  context, fenced-code text fallback, and tool-result applied/rejected tracking. README gained a
  "Works with Llama out of the box" section, and the supported-tools table now lists Llama and
  Antigravity.
- **Base extension 1.3.9** — filesystem-watcher capture path (catches agent edits written
  directly to disk, e.g. Antigravity), Antigravity/Gemini tool recognition, AGENTS.md
  instruction-scan coverage, and the webview-focus "Insert at cursor" fix (see the base
  extension's own CHANGELOG).
- **CI hardening** — base-extension job now typechecks (`npm run typecheck`), builds the bundle,
  and uploads a VSIX artifact per PR (explicit `out/` path so committed .vsix files can't be
  scooped up); the backend SQLite job installs `lineagelens-verifier` so the capsule↔verifier
  roundtrip test stops silently skipping; job timeouts, npm caching, and a cancel-in-progress
  concurrency group were added.

### Fixed
- TSA notarization fails closed when no TSA is configured (no fabricated receipts); pinned by
  `tests/test_tsa_fail_closed.py`.

## [1.2.x] - 2026-06-04

Consolidated summary of backend/proxy additions since the initial release (intermediate
versions were not individually logged).

### Added
- Dynamic model routing in the proxy — request classifier (simple/standard/complex), per-workspace
  routing policies (`/policies/routing`), and recorded cost-savings estimates.
- Evidence-weighted confidence engine — five-signal score (0.0–1.0) with stored breakdown.
- Provenance hash chain (Plus/Max) — `record_hash`/`prev_hash` columns and `GET /integrity/verify`
  for tamper detection.
- Signed AI Bill of Materials — `POST /integrity/aibom` (HMAC-SHA256).
- Agent Trace interchange — `GET /export/agent-trace` and `POST /import/agent-trace`
  (cursor/agent-trace 0.1.0).
- Field-level encryption for sensitive columns (GitHub tokens, webhook secrets).
- Admin auto-seed (`ADMIN_SEED_*`) and invite-link onboarding (`/auth/invite`, `/invite-accept`) —
  no curl required.
- `lineagelens-config/tiers.json` capability contract; `/health` now reports `productMode`,
  `tierLabel`, and `mcp`.

### Changed
- Consolidated risk scoring into a single `risk_service` module.
- Renamed "semantic search" to "keyword search" where embeddings are hash-based.
- Split the dashboard into `dashboard.html` + `dashboard.js` with a locally bundled Chart.js
  (CSP-friendly).

## [0.0.1] - 2026-04-15

### Added
- AI insertion detection with configurable line threshold
- Local LLM HTTP proxy for prompt/response capture
- Prompt-to-code correlation engine (timing + content similarity)
- Context snapshot capture (imports, manifests, environment)
- AST normalization via tree-sitter
- Deterministic local embeddings
- Local storage mode (zero-setup, offline)
- Backend storage mode (Postgres + pgvector + Neo4j)
- Provenance sidebar with explanation support
- Provenance search panel with filtering
- Local Ollama integration for explanations
- WebSocket + HTTP ingest with retry
- JWT authentication with workspace isolation
- Lineage graph tracking (EXTENDED, REFACTORED, MOVED, SPLIT, DELETED)
- GitHub Action for PR provenance review
- Rate limiting for HTTP and WebSocket traffic
