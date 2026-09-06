# LineageLens — Product Roadmap & Deep-Dive Analysis

> Prioritized build plan, grounded in a fresh read of the actual codebase.
> Author context: solo maintainer, evenings/weekends, cost-conscious managed stack.
> Last updated: 2026-05-30 — supersedes the 2026-05-29 draft, which was based on now-stale findings.

---

## 0. What changed since the last roadmap (read this first)

The previous roadmap's headline was that inline AI blame was an unfinished "41-line stub."
**That is no longer true.** A direct read of the code today shows:

- `lineagelens-src/provenanceCodeLens.ts` is a **complete 215-line provider**. It renders
  `⚡ AI-generated (model · risk)` CodeLens, resolves the line via stored cursor position
  with a first-line text-match fallback, and ships a second `ProvenanceQuickActionProvider`
  with **Explain / Flag / Review** actions. The brand's core feature works in the full edition.
- The "stray junk to delete" (`lineagelens-prafea`, `pracodx`, `prav2`) are **not** empty
  artifacts — they are 28–64 KB of real markdown (system documentation, a launch plan, and
  paste-ready launch copy). They're missing `.md` extensions, not worthless.

So the old "finish the stub" premise is retired. The real, verified problems are different —
and one of them is more dangerous than anything in the prior draft (see 2.1).

---

## 1. Executive summary

LineageLens is a broad, largely-built platform, not a thin MVP. Verified in-repo:

- **Backend** (`lineagelens-backend`): **33 registered route modules** (34 route files;
  `request_utils.py` is a shared utility, not a router) — auth/invites, SSO/OIDC, RBAC,
  audit, semantic search + facets, analytics, tags, comments, reviews, webhooks, lineage,
  insights, quality, retention/deletion, scheduled reports, saved queries, LLM explain, diff,
  export, GitHub, ingest, API keys, alerts, policies, bulk, setup wizard, WS capture.
- **Proxy** (`lineagelens-proxy`): multi-provider routing (Anthropic/OpenAI/Gemini),
  classifier-driven cost downgrade, pricing, routing-policy cache, per-adapter tests.
- **Full VS Code edition** (`lineagelens-src`): provenance store, **working CodeLens blame**,
  hover, correlation, file timeline, trace panel, diff view, onboarding wizard, insights
  dashboard, reviewer, and **10 agent adapters** (aider, claudeCode, codex, geminiCli,
  cursor, copilot, continue, amazonQ, codeium, cody) — **no Cline adapter yet**.
- **Free Base edition** (`lineagelens-base-extension`): ~900 LOC, local-only insertion
  capture with a list sidebar — **no model, no prompt, no blame UI**.
- Plus an MCP server, k8s/helm, n8n flows, a deploy CLI, a docs folder, and a marketing site.

The product's problem is **not** missing features. It is three things, in order:
**(1) a publishing-identity collision that risks shipping the wrong build, (2) trust gaps
(raw secrets stored, no git-anchored attribution) that block any shared/hosted mode, and
(3) zero discoverability** — the #1 growth blocker per the social audit.

> **The new headline finding:** both `package.json` (root → full `lineagelens-src` build) and
> `lineagelens-base-extension/package.json` declare the **identical** marketplace identity —
> `name: "lineagelens"`, `publisher: "karnatipraveen"`, `version: "1.2.3"`. Two different
> builds compete for the same Marketplace slot. Whichever was published last *is* the public
> product, and it's ambiguous which one that is. This must be resolved before anything else.

---

## 2. Current-state assessment (verified against code, 2026-05-30)

### Genuinely working ✅
- **Inline AI blame (full edition)** — `provenanceCodeLens.ts` renders model + risk CodeLens
  and quick actions. Real, not a stub.
- **Proxy capture & routing** — native tool-call parsing, applied/rejected correlation,
  multi-provider, cost downgrade. Tested.
- **Local correlator** (`lineagelens-src/correlation.ts`) — heuristic text/timing/line-count
  match of editor inserts → capture records. Functional.
- **Agent adapters** — 10 adapters exist and are not stubs (aider, claudeCode, codex,
  geminiCli, cursor, copilot, continue, amazonQ, codeium, cody). **No Cline adapter.**
- **Backend platform** — auth/invites, RBAC, audit, search+facets, lineage, retention, SSO.
- **Security posture** — recent hardening (IPv6 CONNECT allowlist, secret validation, token
  rotation, rate limits).

### Broken / ambiguous / incomplete ⚠️
| Item | Verified state |
|---|---|
| **Marketplace identity collision** | Root and Base `package.json` both = `lineagelens` / `karnatipraveen` / `1.2.3`. Unclear which build ships. **Top risk.** |
| **Free edition shows no provenance** | Base ext captures only `{filePath, insertedCode, linesAdded, confidence, source}` — **no model, no prompt**. Sidebar is a flat list; no CodeLens/hover. The install funnel can't show the killer feature. |
| **Free edition can't show true blame** | Because it never sees the proxy, the Base ext *structurally cannot* show "which model / which prompt." It can only show "likely-AI (confidence, +N lines)." A proxy bridge is required for real blame. |
| **README ↔ reality drift** | Adapters exist but may still read as "planned"; k8s exists. Re-audit and align. |

### Missing / weak ❌
| Area | Gap |
|---|---|
| **Secret redaction** | `ingest.py` does **no** redaction/entropy scrub — prompts/responses stored raw, routinely containing API keys. The proxy has opt-in regex redaction via `PROXY_REDACT_PATTERNS` (defaults to empty/disabled); the retention service soft-redacts timed-out records. The gap is **mandatory backend-level scrub** before any hosted/shared mode is safe. |
| **Git line-range correlation** | `correlation.ts` matches *text/timing*, not committed *line ranges*. No commit-SHA ↔ line-range provenance → blame drifts as files are edited; no durable PR-hunk attribution. |
| **Search quality** | `embedding_provider` default is **`hash`** (semantically meaningless). Single vector over concatenated text; no chunking (token truncation), no hybrid keyword+vector, no rerank. |
| **Embedding pipeline** | Runs on the sync ingest path; no background worker; no backfill. |
| **Distribution** | No hosted demo/sandbox. Docs are a `lineagelens-docs/` folder, not a published site. Marketing site exists but isn't a "try it" surface. |
| **Monetization** | No managed tier (the realistic revenue path for a solo maintainer). |
| **Repo hygiene** | `lineagelens-prafea`/`pracodx`/`prav2` are real docs missing `.md`; should be renamed and moved into `lineagelens-docs/`, not deleted. |

---

## 3. Strategic thesis

1. **Fix the identity before shipping anything.** A feature in the wrong build is invisible.
   Resolve which `package.json` is canonical and how the two editions map to one Marketplace ID.
2. **The free edition is the funnel — make it *prove* the value it can.** It can't show
   model/prompt without a proxy, but it *can* show "AI-generated here, confidence X, +N lines"
   inline via CodeLens. That alone is more than today's flat list. The full edition already
   does the rest; surface a clear upgrade path.
3. **Trust is a feature, and it's the gate.** Redaction + git-anchored attribution unlock the
   hosted demo, the enterprise story, and safe public GIFs. Build these before distribution.
4. **Ship the demo, not just the feature.** Growth is gated on discoverability; every Phase-1
   item should yield a GIF/screenshot/live sandbox for the social flywheel.
5. **Stay one-person-operable.** Managed primitives (Railway/Render/Fly, managed Postgres).
   k8s/helm stay in the repo but don't lead solo growth.

---

## 4. Prioritized roadmap

Effort key: **S** = a weekend · **M** = 1–2 weeks of evenings · **L** = 3–4+ weeks.

### Phase 0 — Stop shipping ambiguity (do this first)
| # | Action | Effort | Why |
|---|---|---|---|
| 0.1 | **Resolve the Marketplace identity collision** — decide the canonical build; give the two editions distinct `name`/`publisher` or a single build with a feature flag; document the mapping | S | Two builds claim one slot today. Highest-risk, lowest-effort fix in the repo. |
| 0.2 | **Confirm what's actually live** — install the published `karnatipraveen.lineagelens` and record which edition users get; reconcile with intent | S | You can't plan the funnel without knowing what's in it. |
| 0.3 | **README ↔ reality re-audit** — adapters, k8s, editions, working CodeLens | S | Stop underselling shipped work; credibility is free. |
| 0.4 | **Rehome the loose docs** — rename `prafea`/`pracodx`/`prav2` → `.md` and move into `lineagelens-docs/` | S | They're real content, not junk. Recover them; declutter root. |

### Phase 1 — Make the free funnel demonstrate value
| # | Feature | Effort | Why |
|---|---|---|---|
| 1.1 | **Inline CodeLens in the Base edition** — `🤖 AI-generated · confidence X% · +N lines` from the local capture store (no proxy needed) | M | The free install finally shows provenance inline, not a flat list. Funnel proof. |
| 1.2 | **Optional local proxy bridge for Base** — if the user runs the proxy, enrich Base captures with model + prompt so blame becomes the *real* "git blame for AI" | M | Bridges the structural gap; turns the free tier into a true upgrade ramp. |
| 1.3 | **In-product upgrade affordance** — Base CodeLens/hover links to "see model + prompt → set up the proxy" | S | Converts free users to Lite/Plus without a sales motion. |

### Phase 2 — Trust & accuracy (unlock hosted + enterprise + safe demos)
| # | Feature | Effort | Why |
|---|---|---|---|
| 2.1 | **Secret redaction on ingest** — regex + entropy scrub before storage; allowlist config; redact-at-rest | M | Prereq for *any* shared/hosted/demo mode and the compliance wedge. Currently raw. |
| 2.2 | **Git line-range correlation** — on commit, map captures → file + line ranges → SHA so blame survives edits and PR hunks attribute durably | M | Upgrades correlation from "text guess" to anchored provenance; enables PR-hunk attribution. |
| 2.3 | **Capture reconciliation/dedup** — merge editor + proxy captures into one canonical record (proxy > editor precedence) | M | Stops double-count / mis-source that undermines the core claim. |
| 2.4 | **PR hunk attribution** (extend `github.py` / provenance-review workflow) — per-hunk "78% AI (claude-opus), prompt: …" comment | M | Turns existing plumbing into a visible, viral artifact. Depends on 2.2. |

### Phase 3 — Search & retrieval quality (make the stored data useful)
| # | Feature | Effort | Why |
|---|---|---|---|
| 3.1 | **Real embeddings default for Lite** — bundle a small local model so search works without an OpenAI key | M | `hash` default = meaningless semantic search out of the box. |
| 3.2 | **Chunking + background embedding worker + backfill** | M | Long convos currently truncate; sync hot path is slow. |
| 3.3 | **Hybrid search (keyword + vector) + rerank top-k** | M | Vector-only misses exact identifiers/error strings. |

### Phase 4 — Coverage (widen the moat)
| # | Feature | Effort | Why |
|---|---|---|---|
| 4.1 | **Browser capture** (extension/bookmarklet) for ChatGPT/Claude/Gemini web → editor | L | Largest untracked surface; most devs still paste from web UIs. |
| 4.2 | **CLI wrapper** `lineagelens run -- <tool> "…"` | S | Capture without full proxy setup; lowers onboarding cliff. |
| 4.3 | **Harden Aider/Continue adapters end-to-end** | M | Convert "shipped" status into "supported with confidence." (Cline adapter not yet added.) |

### Phase 5 — Distribution & monetization (convert work into users/revenue)
| # | Feature | Effort | Why |
|---|---|---|---|
| 5.1 | **Hosted sandbox / live demo** (read-only seeded instance) — *after* 2.1 redaction | M | #1 growth lever per social audit; "try without installing." |
| 5.2 | **One-click deploy templates** (Railway/Render/Fly for Lite/Plus) | S | Removes Docker friction. |
| 5.3 | **Publish docs site** from `lineagelens-docs/` | S | Overdue; SEO + credibility. |
| 5.4 | **AI Bill of Materials (AIBOM) export** — per-release: % AI code, models, prompts, risk hotspots (JSON/SARIF/PDF) | M | Enterprise wedge on existing export/lineage. |
| 5.5 | **Managed cloud tier** (opt-in) | L | Realistic revenue path; gate on traction. |

---

## 5. Recommended first slice (next ~2 weeks)

A tight, shippable, shareable sequence that de-risks the product:

1. **0.1 + 0.2** — resolve the identity collision and confirm what's actually published.
   *(One evening; unblocks everything.)*
2. **1.1 Base-edition CodeLens** — the free install shows `🤖 AI-generated · confidence · +N lines`
   inline instead of a flat list.
3. **2.1 Secret redaction on ingest** — so the next step (a public demo) is safe to share.
4. Record the GIF: install free ext → AI writes code → inline marker appears → open full
   edition → model + prompt per line. Publish to Dev.to / X / Product Hunt (feeds the social
   strategy's distribution gap).

Outcome: the public product is unambiguous, the free funnel demonstrates value, secrets are
safe to show, and you have a demo asset in hand — the biggest unlock available right now.

---

## 6. Success metrics (pick one primary — see open decisions)
- **Funnel:** free installs → proxy setups → Lite/Plus deploys (the Phase-1 conversion path).
- **Activation:** % of installs that capture ≥1 record in week one.
- **Distribution:** GitHub stars, Marketplace installs, demo-sandbox sessions.
- **Trust:** redaction coverage (% ingests scrubbed), attribution accuracy (correlated vs total).

---

## 7. Key risks
- **Identity collision ships the wrong edition** — addressed by Phase 0; until then, treat the
  public product as "unknown."
- **Raw-secret storage** — a single leaked prompt in a public demo is reputational damage; 2.1
  gates 5.1.
- **Blame drift** — without 2.2, text-matched blame degrades as files change; demos may show
  wrong lines.
- **Solo bandwidth** — Phases are ordered so each one ships independently; do not start Phase 5
  before Phase 2.

---

## 8. Explicitly NOT now
- New backend route modules (surface already exceeds the user base).
- Cursor/Windsurf *full agent* capture (proprietary backends; editor-level is the ceiling).
- Leading with k8s/helm (keep it; not the solo-growth path).
- Multi-tenant SaaS architecture and SOC 2 (defer to 5.5 / post-PMF).
- Deleting `lineagelens-prafea`/`pracodx`/`prav2` — they're real docs; rehome them (0.4) instead.

---

## 9. Open decisions
- **Edition strategy** — one build with a flag, or two distinctly-named Marketplace listings?
  (Drives all of Phase 0.)
- **Primary success metric** — installs vs proxy-setups vs stars vs enterprise leads? Reorders
  Phase 4 vs 5.
- **Local-embedding model** for 3.1 — bundle size vs quality.
- **AIBOM format** — emerging CycloneDX-style standard vs pragmatic JSON first.
- **Personal vs product brand** for demo/publishing identity (ties to the social strategy).

---

## 10. Implementation details by phase

> Per-phase implementation guide: exact files, steps, tests, risks, and expected user outcome.

### Phase 0 — Publishing Identity & Docs

**Files touched:**
- `package.json` (root) — publisher/name/version
- `lineagelens-base-extension/package.json` — publisher/name/version
- `README.md` — install instructions, adapter table, roadmap section
- `lineagelens-prafea`, `pracodx`, `prav2` → rename + move to `lineagelens-docs/`

**Steps:**
1. Resolve the edition strategy (open decision 9.1: one Marketplace listing vs two distinctly-named ones)
2. Bump whichever `package.json` changes; document the mapping in README "Install" section
3. `git mv lineagelens-prafea lineagelens-docs/lineagelens-prafea.md` (repeat for pracodx, prav2)

**Tests:** `npm run compile` in both extension directories; verify no broken `package.json` fields

**Risks:** Publishing the wrong edition invalidates install metrics — confirm what's currently live (0.2) before changing 0.1

**User-visible outcome:** One unambiguous Marketplace listing; install command in README installs the intended edition

---

### Phase 1 — Base Edition Inline CodeLens

**Files touched:**
- `lineagelens-base-extension/src/extension.ts` — register CodeLens provider
- `lineagelens-base-extension/package.json` — add `codeLens` provider contribution
- `lineagelens-base-extension/src/store.ts` — read-only (CaptureStore already exists)
- `lineagelens-base-extension/src/__tests__/` — add provider unit tests

**Steps:**
1. Implement `BaseProvenanceCodeLensProvider implements vscode.CodeLensProvider`; read from `CaptureStore`; render `🤖 AI-generated · confidence X% · +N lines`
2. Use `insertedCode` text-match heuristic (same approach as `lineagelens-src/correlation.ts`)
3. Register in `extension.ts` `activate()` scoped to configured languages
4. Add second CodeLens line as upgrade affordance: "See model + prompt → set up proxy"
5. Write Jest tests covering: provider returns lens at correct line, zero captures returns empty

**Tests:** `npm test` in `lineagelens-base-extension/`; manual `.vsix` install smoke-test

**Risks:** Text-match heuristic has false positives on refactored code; document the limitation in hover tooltip

**User-visible outcome:** Free install shows inline `🤖 AI-generated` markers in the editor; first "wow" moment without a backend

---

### Phase 2 — Mandatory Redaction & Trust

**Files touched:**
- `lineagelens-backend/app/api/routes/ingest.py` — call redactor before DB write
- `lineagelens-backend/app/services/ingest_normalizer.py` — add `IngestRedactor` class
- `lineagelens-backend/app/core/config.py` — add `REDACT_PATTERNS` workspace setting
- `lineagelens-backend/app/db/models.py` — add `redacted_at_ingest: bool` field (distinct from the existing `is_redacted` field, which is set by the **retention service** when records age out of policy; `redacted_at_ingest` marks ingest-time secret scrubbing so the two cases are independently queryable)
- `lineagelens-proxy/proxy.py` — add example patterns to env-var comment; no logic change
- `lineagelens-backend/tests/` — add redaction coverage tests

**Steps:**
1. `IngestRedactor`: compile configurable regex list + Shannon-entropy scrub for tokens > 20 chars high-entropy; replace matches with `[REDACTED]`
2. Invoke redactor in `ingest.py` on `prompt_messages`, `raw_model_response`, `surrounding_context`, `context_snapshot` before any `session.add()`
3. Persist `record.redacted_at_ingest = True` on the record so UI can surface a trust badge (do **not** reuse `is_redacted` — that field is owned by the retention service and has different semantics: it means "sensitive fields were zeroed after aging out of policy," not "cleaned at ingest")
4. Add workspace-level allowlist in config so teams can whitelist known-safe patterns
5. Update `.env.example`: add commented-out `PROXY_REDACT_PATTERNS` with example patterns

**Tests:** `pytest lineagelens-backend/tests/ -k redact`; inject `sk-abc123` in test payload; assert stored record contains `[REDACTED]`

**Risks:** Entropy threshold too low → legitimate code (base64 blobs, JWTs) gets redacted; start conservative, make threshold configurable

**User-visible outcome:** Dashboard shows "redacted" badge; safe to record demos/share captures publicly; prereq for Phase 5 hosted sandbox

---

### Phase 3 — Git Line-Range Attribution

**Files touched:**
- `lineagelens-src/correlation.ts` — emit `git_commit_sha` after correlation
- `lineagelens-backend/app/api/routes/github.py` — per-hunk AI-% PR comments
- `lineagelens-backend/app/db/models.py` — add `git_commit_sha`, `git_line_start`, `git_line_end`
- `lineagelens-backend/app/services/provenance_service.py` — line-range lookup
- Git hook or GH Actions workflow — trigger attribution on commit

**Steps:**
1. Post-commit hook (or GH Actions `push` trigger): run `git diff HEAD~1 --unified=0`; parse hunk headers; match capture records by `filePath` + `insertedCode` snippet; write `git_commit_sha` / `git_line_start` / `git_line_end`
2. Extend `github.py` PR-comment route: for each hunk in the PR diff, query records in the line range → emit "78% AI (claude-opus), prompt: …" comment
3. Expose `GET /provenance?file=<path>&line=<n>` endpoint resolving via stored line ranges

**Tests:** `pytest` with a fixture git repo; assert `git_commit_sha` populated after simulated commit; assert PR comment body contains model name

**Risks:** Line-range drift on rebases/squashes — document limitation; force-push scenarios skip the hook

**User-visible outcome:** PR comments show per-hunk AI attribution; `git blame`-style provenance survives file edits

---

### Phase 4 — Search Quality (Embeddings)

**Files touched:**
- `lineagelens-backend/app/core/config.py` — change `EMBEDDING_PROVIDER` default from `hash` to `local-minilm`
- `lineagelens-backend/app/services/embedding_service.py` — add local model provider
- `lineagelens-backend/app/services/ingest_normalizer.py` — add chunking (≤512 tokens)
- `lineagelens-backend/app/api/routes/search.py` — add hybrid BM25 + vector search with RRF reranking
- `lineagelens-backend/requirements.txt` — add `sentence-transformers`

**Steps:**
1. Add `sentence-transformers` `all-MiniLM-L6-v2` (22 MB download on first use, ~80 MB RAM); change `EMBEDDING_PROVIDER` default
2. Chunk `inserted_code` + `prompt_messages` into ≤512-token windows before embedding; store chunk vectors separately
3. Move embedding computation off the sync ingest path to an `asyncio` background task (or Celery if already in stack)
4. Add backfill script `lineagelens-scripts/backfill_embeddings.py`: re-embed all records where `embedding_model = "hash"`
5. Add BM25 index (PostgreSQL `tsvector` for Lite/Plus); fuse keyword + vector scores with RRF in `search.py`

**Tests:** `pytest` with semantic search fixture; query "rate limiting" → top result is the rate-limit capture; `rg 'default="hash"' app/core/config.py` returns empty

**Risks:** `sentence-transformers` adds ~400 MB to Docker image; make the local model opt-in vs required, document the `OPENAI_API_KEY` path as the alternative

**User-visible outcome:** Search returns semantically relevant results out of the box; "prompt about rate limiting" finds the rate-limit insert

---

### Phase 5 — Demo & Distribution

**Files touched:**
- `lineagelens-scripts/seed_demo.py` — new: populate 50 synthetic records
- `lineagelens-deploy/railway.json` — new: one-click Railway deploy template
- `lineagelens-docs/` — publish via GitHub Pages or Vercel (`vercel.json` + `mkdocs.yml`)
- `lineagelens-backend/app/api/routes/export.py` — add `?format=aibom` output

**Steps:**
1. `seed_demo.py`: generate synthetic provenance records across 5 languages and 4 tools with redacted prompts; seed a read-only Postgres instance
2. `railway.json`: define services (`proxy`, `backend`, `dashboard`); pre-fill env vars; expose ports 8787 and 8788
3. Publish docs: add `mkdocs.yml` pointing at `lineagelens-docs/`; add GitHub Actions `pages.yml`
4. AIBOM export: `GET /export?format=aibom` → `{ ai_line_pct, models: [], prompts_sample: [], risk_hotspots: [] }` in JSON; SARIF wrapper optional

**Tests:** `curl http://localhost:8787/api/v1/provenance` returns seeded records; `mkdocs build` succeeds; AIBOM JSON validates against schema

**Risks:** Demo seeded with un-redacted content → reputational damage; 2.1 (mandatory redaction) MUST land before 5.1 (hosted sandbox)

**User-visible outcome:** "Try LineageLens" link in README leads to live seeded dashboard; Railway one-click deploy lowers onboarding from 20 min to 2 min
