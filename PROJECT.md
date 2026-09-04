# Project: LineageLens Flaw Identification & Reproduction

## Architecture & Subsystems
1. **lineagelens-mcp**: Python MCP Server for LLM agent integration (tools, prompts, resources, risk checking, search, auth).
2. **lineagelens-src**: VS Code Extension (TypeScript/Esbuild, local HTTP proxy, prompt correlation engine, local storage, reviewer).
3. **lineagelens-backend**: FastAPI / SQLAlchemy / PyNaCl / Ed25519 backend service (ingest, hash chain, attestations, governance engines, search).

## Feature Inventory & Flaw Catalog

| # | Flaw / Feature | Component | Description | Milestone | Source |
|---|----------------|-----------|-------------|-----------|--------|
| 1 | `check_file_risk` Flat Search Result Bug | `lineagelens-mcp` | Missing `record` key causes `riskScore` to default to 0; all files misclassified as "low" risk. | M1 | survey_mcp |
| 2 | `_pct` Percentage Formatting Distortion | `lineagelens-mcp` | Multiplies raw numeric values `<= 1.0` by 100, distorting risk representation (e.g. 0.85 formatted as 85.0%). | M1 | survey_mcp |
| 3 | API Key Header Rejection (401) | `lineagelens-mcp` | Backend query endpoints reject `X-API-Key` with HTTP 401; setting `LINEAGELENS_API_KEY` breaks all tool calls. | M1 | survey_mcp |
| 4 | `list_workspaces` Null Iteration Crash | `lineagelens-mcp` | Backend returning `{"workspaces": None}` causes `TypeError` during iteration. | M1 | survey_mcp |
| 5 | Unsafe Score Format String Crash | `lineagelens-mcp` | String risk scores in search results cause `{score:.3f}` formatting to raise `TypeError`. | M1 | survey_mcp |
| 6 | Gzip Proxy Payload Corruption | `lineagelens-src` | Proxy fails to decompress `gzip`/`deflate` bodies, corrupting UTF-8 strings and breaking Levenshtein prompt correlation. | M2 | survey_src |
| 7 | Document Change Event Race Condition | `lineagelens-src` | Async update of `previousDocumentTexts` in `finally` block causes state desynchronization under rapid edits. | M2 | survey_src |
| 8 | LocalStorage Concurrent Write Overwrite | `lineagelens-src` | `updateLineageFromLatestCommit` and `exportAuditCsv` call `writeStore` without acquiring `writeLock`. | M2 | survey_src |
| 9 | Reviewer Custom API URL Fallback | `lineagelens-src` | `openai-compatible` provider settings without legacy config fall back to hardcoded OpenAI endpoint. | M2 | survey_src |
| 10 | Line Threshold Enter Keystroke Flaw | `lineagelens-src` | Single Enter keystrokes trigger full insertion detection when `lineThreshold` is 1. | M2 | survey_src |
| 11 | Hash-Chain Ingest Lock Contention | `lineagelens-backend` | `SELECT FOR UPDATE` on workspace tip record creates write serialisation bottlenecks under parallel ingestion. | M3 | survey_backend |
| 12 | In-Memory Facet Aggregation Scaling | `lineagelens-backend` | Ingest/search facet computation loads up to 2,000 file paths into memory, scaling CPU/RAM linearly. | M3 | survey_backend |
| 13 | Comprehensive Issue Report | Documentation | Produce `issue_report.md` detailing root cause, impact, reproduction steps, and remediation for all 12 flaws. | M4 | original_request |
| 14 | Automated Reproduction Runner | Test Infra | Ensure all automated reproduction test scripts in `reproduction_tests/` run and fail cleanly to prove flaws. | M5 | original_request |

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | `reproduction_tests_mcp` | Write automated reproduction test scripts in `reproduction_tests/` for MCP flaws 1-5 | none | DONE |
| M2 | `reproduction_tests_src` | Write automated reproduction test scripts in `reproduction_tests/` for Extension flaws 6-10 | none | DONE |
| M3 | `reproduction_tests_backend` | Write automated reproduction test scripts in `reproduction_tests/` for Backend flaws 11-12 | none | DONE |
| M4 | `issue_report_compilation` | Compile `issue_report.md` at project root covering all 12 flaws | M1, M2, M3 | DONE |
| M5 | `e2e_verification_and_audit` | Verify execution of all reproduction scripts and validate completeness of `issue_report.md` | M4 | IN_PROGRESS |

## Interface Contracts & Layout

- **Reproduction Test Scripts**: Located in `c:\Users\karna\OneDrive\Desktop\Lineagelens\reproduction_tests\`.
  - Must run standalone via `python` or `node`.
  - Must exit with non-zero exit code (or fail assertions) when demonstrating the flaw.
  - Must NOT modify or destructively alter source code or application state.
- **Issue Report**: Located at `c:\Users\karna\OneDrive\Desktop\Lineagelens\issue_report.md`.
