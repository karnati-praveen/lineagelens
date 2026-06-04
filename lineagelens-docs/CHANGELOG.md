# Change Log

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
