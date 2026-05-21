# LineageLens

**Self-hosted provenance for AI-generated code.**

Captures the prompt, model, tool, file, and accept/reject status from every AI coding session — across Claude Code, Codex CLI, Gemini CLI, and VS Code-based editors — and stores it in your own infrastructure so you can answer "which code did AI write, with what prompt, by which model" without sending anything to a SaaS.

![LineageLens dashboard](lineagelens-docs/assets/hero.png)

## Install (pick one)

```bash
# 1. Free VS Code extension — also works in Cursor and Windsurf
code --install-extension karnatipraveen.lineagelens

# 2. Lite backend — single Docker container with proxy + dashboard
git clone https://github.com/karnati-praveen/lineagelens && cd lineagelens
bash lineagelens-scripts/quickstart-lite.sh

# 3. Plus / Max — Postgres team backend, optional Neo4j lineage graph
bash lineagelens-scripts/quickstart-plus.sh    # or quickstart-max.sh
```

---

## Why this exists

It's 2026. You used Claude Code to write the JWT verifier in `auth.py` on Tuesday, Cursor to refactor the payment handler on Wednesday, and Copilot to fill in the test stubs Thursday morning. By Friday your PR has 400 lines of code across 12 files. Your reviewer asks: "did you write this or did AI?" You answer honestly: "mostly AI." They ask: "which one? With what prompt?" And you have no idea.

Git blame says you wrote it. Git history was designed in 2005, before any of this existed.

GitHub Copilot Audit Log (launched Q4 2025) and Cursor's team activity dashboard partially fill the gap — for *their* tools only. If your team uses Claude Code in the terminal AND Copilot in VS Code, you need two SaaS subscriptions and you still don't see the third tool one of your engineers is trying out. And in any case, your prompts get sent to the vendor. For fintech, healthtech, and defense teams that's not acceptable.

LineageLens is the open-source, self-hosted answer. The universal proxy parses each provider's *native* tool-calling protocol — Anthropic `tool_use` blocks, OpenAI Responses API `function_call` items with the `apply_patch` DSL, and Gemini `functionCall` parts — and correlates each edit with the next turn's `tool_result` so every capture is resolved to `applied`, `rejected`, or `errored`. Not "the AI said this once" — but "this edit landed and the harness confirmed it."

Self-hosted. MIT licensed. Free Base extension on the VS Code Marketplace and Open VSX. Backend tiers from one Docker container up to a Postgres + Neo4j compliance setup.

---

## Supported tools

| Tool | Capture method | What you get |
|---|---|---|
| **Claude Code** (terminal) | Proxy (`ANTHROPIC_BASE_URL`) | Full — prompt, model, edit, applied/rejected status |
| **OpenAI Codex CLI** | Proxy (`OPENAI_BASE_URL`) — parses Responses API + `apply_patch` DSL | Full |
| **Gemini CLI** | Proxy — parses `functionCall` / `functionResponse` | Full |
| **Goose** (Block) | Proxy — rides on Anthropic or OpenAI provider format | Full |
| **VS Code Copilot** | Free VS Code extension | Editor-only — file path + inserted lines, no prompt |
| **Cursor** | Free VS Code extension (Cursor is a VS Code fork) | Editor-only — Cursor's agent traffic goes to api.cursor.sh and can't be proxied |
| **Windsurf** | Free VS Code extension (Windsurf is a VS Code fork) | Editor-only — same proprietary backend reason as Cursor |
| **Continue** | Proxy when configured for a native-tool-call provider | Full or editor-only depending on the model used |
| **Aider** | *Not yet — Tier 2 adapter planned (git-log + .aider.chat.history.md)* | — |
| **Cline / Roo Code** | *Not yet — Tier 2 adapter planned (parses Cline's XML tool format)* | — |
| **GitHub Copilot CLI** | Not supported. Proprietary endpoints, no public route. | — |
| **Amazon Q Developer** | Not supported. AWS proprietary protocol. | — |

If the tool you use isn't here, open an issue. The four-layer attribution model in [lineagelens-docs/lightweight-adapters.md](lineagelens-docs/lightweight-adapters.md) explains what's possible and what isn't.

---

## Operating modes

| Mode | Use case | Storage | Setup |
|---|---|---|---|
| **Base** | Solo developer, no backend, fully local | VS Code global state (JSON) | Install the extension |
| **Lite** | Small team (≤ 10), one box, low friction | SQLite, single Docker container | `bash quickstart-lite.sh` |
| **Plus** | Teams (10–100), governance dashboard, MCP server, GitHub Actions risk gate | PostgreSQL + pgvector | `bash quickstart-plus.sh` |
| **Max** | Compliance-heavy teams, audit lineage graph | PostgreSQL + pgvector + Neo4j | `bash quickstart-max.sh` |

---

## Quick start (Lite)

```bash
git clone https://github.com/karnati-praveen/lineagelens
cd lineagelens
bash lineagelens-scripts/quickstart-lite.sh
```

Open <http://localhost:8787/setup>, create an admin in the wizard, then point any AI CLI tool at the proxy:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8788
export OPENAI_BASE_URL=http://localhost:8788
```

Use Claude Code (or any of the supported CLI tools) as normal. Open <http://localhost:8787/dashboard> to see captures appear.

Plus and Max have the same shape — see [lineagelens-docs/shipping-modes.md](lineagelens-docs/shipping-modes.md) for the full setup including Postgres / Neo4j requirements, the GitHub Actions risk-gate workflow, and the MCP server configuration.

---

## Architecture

```
                ┌─────────────────────┐
                │  Your AI tool       │  (Claude Code, Codex CLI,
                │  (CLI or editor)    │   Gemini CLI, Cursor, etc.)
                └──────────┬──────────┘
                           │
        proxy path ◄───────┴───────► extension path
                │                        │
   ┌────────────▼────────────┐  ┌────────▼─────────┐
   │  LineageLens proxy      │  │  VS Code         │
   │  (parses tool_use /     │  │  extension       │
   │   function_call /       │  │  (file watcher)  │
   │   functionCall)         │  └────────┬─────────┘
   └────────────┬────────────┘           │
                │                        │
                └────────────┬───────────┘
                             │
                  ┌──────────▼──────────┐
                  │  LineageLens        │
                  │  backend (FastAPI)  │
                  └──────────┬──────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
       ┌────▼────┐    ┌──────▼──────┐    ┌────▼────┐
       │ SQLite  │    │ PostgreSQL  │    │  Neo4j  │
       │ (Lite)  │    │ (Plus/Max)  │    │  (Max)  │
       └─────────┘    └─────────────┘    └─────────┘
```

The proxy intercepts traffic going to the AI provider. It does *not* modify requests or responses — it forwards them untouched. It also parses each provider's native tool-calling protocol to extract structured edits, then correlates them with the next turn's tool_result to determine whether the edit landed.

The VS Code extension is independent. It watches `onDidChangeTextDocument` for 4+ line insertions and stores them locally or POSTs them to the backend when configured. No proxy involvement, no API key required for the free Base mode.

Full architecture notes including the four-layer attribution model and the per-adapter parser logic: [lineagelens-docs/architecture.md](lineagelens-docs/architecture.md).

---

## What this isn't

- **Not a SAST scanner.** The risk score is heuristic — based on file path, language, keyword density, and known-risky patterns. It is *not* a replacement for Snyk, Semgrep, or your security team. Pair it with one; don't replace one with this.
- **Not enterprise-ready in v1.1.5.** RBAC and SSO exist but haven't been validated against SOC 2 / ISO 27001 controls. Don't tell your auditor it's compliant. Treat it as a developer-feedback tool until that work is done.
- **Not multi-tenant.** One LineageLens instance serves one workspace (or one organization with a small number of workspaces). If you need to host this for multiple unrelated customers, run separate instances. There is no tenant-level data isolation beyond `workspace_id` in JWT claims.
- **Single bus factor.** This is one person's project. Bug reports get fast turnaround during the week; weekends are slower. If you're an enterprise considering this, factor in the maintainer count.
- **Cursor agent mode and Copilot CLI are partially out of scope.** Their requests route through their own proprietary backends (api.cursor.sh, api.githubcopilot.com) and can't be intercepted at the network layer. You get editor-level capture via the VS Code extension; you don't get the prompt or model.

I'd rather you find this list before you find these limits in production.

---

---

## Roadmap

Short, no dates promised. Things that are next, roughly in order of intent:

- **Tier 2 adapters** — Aider via git-log fingerprinting, Cline / Roo Code via XML tool-format parser, Continue's text-fallback path.
- **CLI wrapper** — `lineagelens run -- <ai-tool> "..."` for capture without setting up a proxy. Useful for batch/CI usage.
- **Risk scoring v2** — heuristic + lightweight static analysis. Still not SAST; closer to "this file is sensitive" detection.
- **OpenVSX listing** — already submitted, awaiting verified-namespace approval.
- **Dedicated docs site** — current docs are scattered across `lineagelens-docs/`; a single navigable site is overdue.

Things deliberately *not* on the roadmap:

- A hosted SaaS version (until 10+ teams are running self-hosted in production).
- Cursor or Windsurf agent-mode full capture (their proprietary backends make this structurally fragile; not worth the maintenance cost).
- AIBOM compliance certification (real money in this someday, but post-launch).

---

If you're trying this and it doesn't work, please open an issue. Hard for me to fix what I don't know is broken.
