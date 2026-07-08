# LineageLens Tier Capabilities

This document is the human-readable companion to [`lineagelens-config/tiers.json`](../lineagelens-config/tiers.json), which is the machine-readable source of truth. Keep both in sync when adding features.

---

## Quick comparison

| Capability | Base | Lite | Plus | Max |
|---|:---:|:---:|:---:|:---:|
| **VS Code extension** | ✅ | ✅ | ✅ | ✅ |
| **Local JSON storage** | ✅ | — | — | — |
| **Proxy capture** | — | ✅ | ✅ | ✅ |
| **Prompt capture** | — | ✅ | ✅ | ✅ |
| **Model capture** | — | ✅ | ✅ | ✅ |
| **SQLite backend** | — | ✅ | — | — |
| **PostgreSQL backend** | — | — | ✅ | ✅ |
| **Dashboard** | — | ✅ | ✅ | ✅ |
| **Setup wizard** | — | ✅ | ✅ | ✅ |
| **Team users / workspaces** | — | — | ✅ | ✅ |
| **RBAC (admin/member)** | — | — | ✅ | ✅ |
| **Reviews & comments** | — | — | ✅ | ✅ |
| **Audit log** | — | — | ✅ | ✅ |
| **Reports & webhooks** | — | — | ✅ | ✅ |
| **API keys** | — | — | ✅ | ✅ |
| **MCP server** | — | — | ✅ | ✅ |
| **GitHub Actions gate** | — | — | ✅ | ✅ |
| **Provenance integrity (hash chain)** | — | — | ✅ | ✅ |
| **AI-BOM export (signed)** | — | — | ✅ | ✅ |
| **SSO / OIDC** | — | — | — | ✅ |
| **Retention & redaction policy** | — | — | — | ✅ |
| **Vector / semantic search** | — | — | — | ✅ |
| **Neo4j graph lineage** | — | — | — | ✅ |
| **Kubernetes / Helm** | — | — | — | ✅ |

---

## Tier details

### Base — LineageLens Base Extension

**Audience:** Individual developer who wants zero-setup AI insertion tracking.

**Backend mode:** none — all data stays in VS Code `globalState`.

**What it captures:**
- File path, inserted code, lines added, confidence score, and source heuristic.
- Does **not** capture the prompt sent to the AI or the model name (no proxy).

**Known limitations:**
- No inline CodeLens blame (planned).
- No prompt or model attribution without a proxy.

**Package target:** `lineagelens-releases/base/`
**npm command:** `npm run package:base`
**Extension package ID:** `karnatipraveen.lineagelens-base`

---

### Lite — Local self-hosted tier

**Audience:** Individual developer or small team (≤ 10) who wants full local provenance on one box.

**Backend mode:** `BACKEND_MODE=solo`

**What it adds over Base:**
- Full proxy capture: prompt, model, file, status, risk score, timestamp.
- SQLite backend — single Docker container, no external database.
- Basic dashboard at `http://localhost:8787/dashboard`.
- First-boot setup wizard at `http://localhost:8787/setup`.
- All 11 adapter detectors (Claude Code, Codex CLI, Gemini CLI, Goose, VS Code editors, Continue).

**Search:** Keyword-only. `VECTOR_SEARCH_ENABLED=false`.

**What it does NOT include:** Team users, RBAC, MCP server, GitHub Actions gate, SSO, Neo4j.

**Package target:** `lineagelens-releases/lite/`
**npm command:** `npm run package:lite`
**Docker Compose:** `lineagelens-deploy/docker-compose.lite.yml`

---

### Plus — Team governance tier

**Audience:** Small team (10–100 developers) with a private shared backend and governance needs.

**Backend mode:** `BACKEND_MODE=team`

**What it adds over Lite:**
- PostgreSQL backend (replaces SQLite).
- Multi-user workspace with RBAC (admin / member roles).
- Review queue, comments, and annotation on records.
- Audit log (every action logged with user, IP, timestamp).
- Reports, scheduled exports, webhooks.
- API key management with scopes and expiry.
- MCP server for Claude Code / Cursor / Continue integration.
- GitHub Actions gate: block high-risk AI code in CI pipelines.

**Search:** Keyword-only. `VECTOR_SEARCH_ENABLED=false`. The pgvector image is used but vector search is not activated — upgrade to Max for semantic search.

**What it does NOT include:** SSO/OIDC, retention/redaction policies, Neo4j, vector search, Kubernetes.

**Package target:** `lineagelens-releases/plus/`
**npm command:** `npm run package:plus`
**Docker Compose:** `lineagelens-deploy/docker-compose.plus.yml`

---

### Max — Compliance / enterprise tier

**Audience:** Regulated team that needs full audit trails, policy enforcement, graph lineage, SSO, and Kubernetes deployment.

**Backend mode:** `BACKEND_MODE=enterprise`

**Why teams upgrade from Plus:** Plus lets you *see and review* AI provenance; Max lets you *enforce, retain, and prove* it. Five concrete Max-only capabilities — surfaced in the dashboard so the upgrade value is visible, not just SSO:

1. **SSO / OIDC** single sign-on (Okta, Auth0, Azure AD, Keycloak).
2. **Retention & redaction policies** — auto-redact prompt contents and purge old records per workspace (Governance → Retention in the dashboard).
3. **Semantic / vector search** across AI-generated code.
4. **Neo4j graph lineage** — track how a code block evolves over commits.
5. **Kubernetes / Helm** deployment for scale.

**What it adds over Plus:**
- Neo4j 5+ for graph lineage — tracks code block evolution as a directed graph.
- pgvector for semantic / vector similarity search.
- SSO/OIDC via OIDC Discovery (works with Okta, Auth0, Azure AD, Keycloak, etc.).
- Retention and redaction policies per workspace (auto-redact after N days).
- Full audit export (up to 10,000 rows).
- Kubernetes / Helm deployment (`lineagelens-k8s/helm/`).
- MCP server includes Neo4j-backed graph lineage data.

**Operational note:** Neo4j requires ~60 seconds on first boot to bake credentials. The quickstart script handles this automatically.

**Embedding quality note:** `EMBEDDING_PROVIDER` defaults to `hash` (deterministic, structurally meaningless). Set `EMBEDDING_PROVIDER=openai` with a valid `OPENAI_API_KEY` to enable real semantic embeddings.

**Not yet validated against:** SOC 2 / ISO 27001 controls — treat as developer-governance tooling until that work is done.

**Package target:** `lineagelens-releases/max/`
**npm command:** `npm run package:max`
**Docker Compose:** `lineagelens-deploy/docker-compose.max.yml`
**Helm chart:** `lineagelens-k8s/helm/`

---

## /health endpoint tier fields

The backend `/health` endpoint always returns:

```json
{
  "status": "ok",
  "productMode": "lite | plus | max",
  "tierLabel": "LineageLens Lite | Plus | Max",
  "mcp": false | true
}
```

Loopback clients (non-production only) also receive:

```json
{
  "environment": "development",
  "backendMode": "solo | team | enterprise",
  "features": {
    "neo4j": false | true,
    "vectorSearch": false | true,
    "lineageStrictMode": false | true,
    "mcp": false | true
  }
}
```

---

## Packaging scripts

| Tier | npm command | Direct script |
|---|---|---|
| Base | `npm run package:base` | `lineagelens-scripts/package-base.ps1` |
| Lite | `npm run package:lite` | `lineagelens-scripts/package-lite.ps1` |
| Plus | `npm run package:plus` | `lineagelens-scripts/package-plus.ps1` |
| Max | `npm run package:max` | `lineagelens-scripts/package-max.ps1` |
| All four | `npm run release` | `lineagelens-scripts/release.ps1` |

---

## What is not yet implemented

These features are **not yet shipped** in any tier:

| Feature | Planned tier | Notes |
|---|---|---|
| Aider adapter | Lite+ | Git-log + `.aider.chat.history.md` fingerprinting |
| Cline / Roo Code adapter | Lite+ | XML tool format parser |
| CodeLens inline blame | Base+ | Shows AI attribution inline in editor |
| Git line-range attribution | Plus+ | Commit SHA ↔ line-range; blame drifts on edits today |
| Mandatory secret redaction | Plus+ | Prompts stored raw by default; opt-in only |
| Real embedding provider default | Max | `hash` default produces meaningless semantic search |
| AI-BOM asymmetric signing (Ed25519) | Plus+ | Current v1 uses HMAC-SHA256; asymmetric upgrade planned |
