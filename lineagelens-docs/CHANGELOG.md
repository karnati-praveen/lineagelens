# Change Log

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
