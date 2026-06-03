<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0d1117,40:1a1a3e,100:0d1117&height=220&section=header&text=LineageLens&fontSize=80&fontColor=58a6ff&animation=fadeIn&fontAlignY=42&desc=Git%20blame%20for%20AI-generated%20code.%20Self-hosted.%20Open%20source.&descSize=20&descAlignY=65&descColor=8b949e" alt="LineageLens" />

<br/>

[![readme-typing-svg](https://readme-typing-svg.demolab.com/?font=Fira+Code&size=20&pause=1200&color=58A6FF&center=true&vCenter=true&width=700&height=50&lines=Which+code+did+the+AI+write%3F;With+what+prompt%3F;Which+model+generated+it%3F;LineageLens+answers+all+three.)](https://lineage-website.vercel.app/)

<br/>

[![VS Code Marketplace](https://img.shields.io/visual-studio-marketplace/i/karnatipraveen.lineagelens?label=VS%20Code%20installs&color=007ACC&logo=visualstudiocode&logoColor=white)](https://marketplace.visualstudio.com/items?itemName=karnatipraveen.lineagelens)
[![GitHub Stars](https://img.shields.io/github/stars/karnati-praveen/lineagelens?style=flat&color=f9a825&logo=github)](https://github.com/karnati-praveen/lineagelens/stargazers)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?logo=opensourceinitiative&logoColor=white)](./LICENSE)
[![Extension Version](https://img.shields.io/visual-studio-marketplace/v/karnatipraveen.lineagelens?label=extension&color=7c3aed)](https://marketplace.visualstudio.com/items?itemName=karnatipraveen.lineagelens)
[![OpenVSX](https://img.shields.io/badge/OpenVSX-available-c084fc)](https://open-vsx.org/extension/karnatipraveen/lineagelens)

<br/>

**[Website](https://lineage-website.vercel.app/) · [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=karnatipraveen.lineagelens) · [Report a Bug](https://github.com/karnati-praveen/lineagelens/issues) · [Roadmap](#roadmap)**

</div>

---

## The problem

It's 2026. You used Claude Code to write the JWT verifier in `auth.py` on Tuesday, Cursor to refactor the payment handler on Wednesday, and Copilot to fill in the test stubs Thursday morning.

By Friday your PR has 400 lines across 12 files. Your reviewer asks: **"Did you write this or did AI?"**

You answer honestly: "Mostly AI."

They ask: **"Which one? With what prompt?"**

And you have no idea.

`git blame` says you wrote it. Git history was designed in 2005, before any of this existed.

---

## What LineageLens does

LineageLens is a **self-hosted proxy + VS Code extension** that sits between your AI tools and the provider APIs. It captures every edit — the prompt that triggered it, the model that generated it, the file it landed in, and whether it was accepted or rejected — and stores everything in your own infrastructure.

```
Before LineageLens:  "Claude wrote... some of it? I think?"
After LineageLens:   prompt=... model=claude-opus-4-5 file=auth.py status=applied
```

No SaaS. No vendor lock-in. No prompts leaving your network. MIT licensed.

---

## Demo

> **[Live demo → lineage-website.vercel.app](https://lineage-website.vercel.app/)**

<!-- Add a demo GIF here by recording your terminal with `asciinema` or `ttyrec`, converting to GIF, and dropping it in the `lineagelens-media/` folder. Suggested command: `agg lineagelens-media/demo.cast lineagelens-media/demo.gif` -->

<div align="center">

```
$ export ANTHROPIC_BASE_URL=http://localhost:8788
$ claude "add rate limiting to the /api/login endpoint"

  LineageLens captured:
  ┌─────────────────────────────────────────────────────┐
  │  model   claude-opus-4-5                           │
  │  prompt  add rate limiting to the /api/login…      │
  │  file    src/routes/auth.py  (+47 lines)           │
  │  status  applied ✓                                 │
  │  risk    HIGH  (auth + network pattern)            │
  └─────────────────────────────────────────────────────┘
```

</div>

---

## Install

| | Easy Mode | Power Mode |
|---|---|---|
| **Setup** | Install extension | + Start proxy |
| **Captures** | File path, inserted code, language | + Prompt, model name, applied/rejected status |
| **Confidence** | ~0.35 | 0.8 – 1.0 |
| **Backend needed?** | Optional | Yes |
| **Status bar** | `LL: Easy` | `LL: Power` |

---

### Easy Mode (default — zero setup)

```bash
code --install-extension karnatipraveen.lineagelens-base
```

Works in **VS Code**, **Cursor**, and **Windsurf**. Captures AI insertions immediately — no proxy, no API key, no account required.

The status bar shows **LL: Easy (local)** — captures are stored in VS Code global state.

**Optional: sync to a backend without the proxy**

If you have a LineageLens backend running (see [Lite / Plus / Max](#lite--plus--max-teams-with-governance-needs) below), you can stream file-level captures to it without touching the proxy:

1. Open VS Code Settings (`Ctrl+,`) and search `lineagelens`
2. Set **`lineagelensBase.backendUrl`** → your backend URL (e.g. `http://localhost:8787`)
3. Set **`lineagelensBase.ingestToken`** → your ingest token from the backend admin panel
4. Set **`lineagelensBase.workspaceId`** → your workspace slug (default: `vscode-capture`)

The status bar switches to **LL: Easy** and captures appear in the dashboard with `capture_status: file_diff` and confidence ~0.35.

---

### Power Mode (full prompt + model capture) {#proxy}

Power Mode requires the proxy running alongside the extension. Start it with the Lite quickstart:

```bash
git clone https://github.com/karnati-praveen/lineagelens
cd lineagelens
bash lineagelens-scripts/quickstart-lite.sh
```

Open **http://localhost:8787/setup**, create an admin, then point your AI tools at the proxy:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8788   # Claude Code / claude CLI
export OPENAI_BASE_URL=http://localhost:8788       # Codex CLI / Goose
```

The extension auto-detects the proxy (polls `/proxy-health` every 30 s) and switches to **LL: Power** — no extension restart needed. Open **http://localhost:8787/dashboard** to see full prompt + model lineage.

---

### Lite / Plus / Max (teams with governance needs)

```bash
bash lineagelens-scripts/quickstart-plus.sh   # Postgres + pgvector + dashboard + MCP server
bash lineagelens-scripts/quickstart-max.sh    # + Neo4j lineage graph for compliance
```

---

## What you capture

| Field | Example |
|---|---|
| Prompt | `"add rate limiting to /api/login"` |
| Model | `claude-opus-4-5` |
| Tool | Claude Code |
| File | `src/routes/auth.py` |
| Lines | `+47 / -3` |
| Status | `applied` / `rejected` / `errored` |
| Risk score | `HIGH` (auth + network pattern) |
| Timestamp | `2026-05-29T14:22:11Z` |

The proxy parses each provider's **native** tool-calling protocol — Anthropic `tool_use` blocks, OpenAI Responses API `function_call` items with the `apply_patch` DSL, and Gemini `functionCall` parts — and correlates each edit with the next turn's `tool_result` to resolve status. Not "the AI said this once" — but "this edit landed and the harness confirmed it."

---

## Supported tools

| Tool | Capture method | What you get |
|---|---|---|
| **Claude Code** (terminal) | Proxy (`ANTHROPIC_BASE_URL`) | Full — prompt, model, edit, applied/rejected |
| **OpenAI Codex CLI** | Proxy (`OPENAI_BASE_URL`) | Full — Responses API + `apply_patch` DSL |
| **Gemini CLI** | Proxy — parses `functionCall` / `functionResponse` | Full |
| **Goose** (Block) | Proxy — rides Anthropic/OpenAI format | Full |
| **VS Code Copilot** | Free VS Code extension | Editor-only — file + inserted lines, no prompt |
| **Cursor** | Free VS Code extension | Editor-only — agent traffic goes to api.cursor.sh |
| **Windsurf** | Free VS Code extension | Editor-only — same proprietary backend |
| **Continue** | Proxy (native-tool-call provider) | Full or editor-only depending on model |
| **Aider** | Planned — git-log + `.aider.chat.history.md` | — |
| **Cline / Roo Code** | Planned — XML tool format parser | — |
| **GitHub Copilot CLI** | Not supported — proprietary endpoints | — |
| **Amazon Q Developer** | Not supported — AWS proprietary protocol | — |

---

## Operating modes

| Mode | Who it's for | Storage | Captures prompt/model | One-liner |
|---|---|---|---|---|
| **Easy (Base)** | Anyone — zero setup, local or backend-synced | VS Code global state / backend | No — file + lines only | `code --install-extension karnatipraveen.lineagelens-base` |
| **Power (Lite)** | Solo dev or team ≤ 10, one-box, best demo tier | SQLite + Docker | Yes — full prompt + model | `bash lineagelens-scripts/quickstart-lite.sh` |
| **Power (Plus)** | Teams 10–100, governance, MCP, GitHub Actions gate | PostgreSQL (keyword search) | Yes | `bash lineagelens-scripts/quickstart-plus.sh` |
| **Power (Max)** | Compliance teams, full audit + graph lineage + SSO | PostgreSQL + Neo4j + pgvector | Yes | `bash lineagelens-scripts/quickstart-max.sh` |

**Search:** Lite and Plus use keyword search. Max adds pgvector similarity search — with the default `EMBEDDING_PROVIDER=hash` this is hash-based (deterministic, not semantic meaning). Set `EMBEDDING_PROVIDER=openai` in `.env` for real semantic embeddings.

Full capability matrix: [`lineagelens-config/tiers.json`](lineagelens-config/tiers.json)

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

The proxy forwards requests untouched — it does not modify them. It also watches responses to resolve each edit's final status from the `tool_result` turn.

The VS Code extension works independently. It watches `onDidChangeTextDocument` for 4+ line insertions and stores them locally or POSTs them to the backend when configured.

---

## Why this matters now

> **41% of all code merged globally in 2025 was AI-assisted** (GitHub Octoverse). Only 29.5% of those commits include explicit AI disclosure.

**EU AI Act (Articles 11, 12, 14)** — enforceable August 2026 — requires enterprises to document which AI model generated code, what specification governed it, and what human review occurred. Violations carry fines of €35M or 7% of global annual revenue.

LineageLens creates that audit trail automatically, at the point of generation, without changing your git workflow.

---

## What this isn't

Being honest about limits is more useful than hiding them.

- **Not a SAST scanner.** The risk score is heuristic — file path, language, keyword density, known-risky patterns. Pair it with Snyk or Semgrep; don't replace them.
- **Not enterprise-ready yet.** RBAC and SSO exist but haven't been validated against SOC 2 / ISO 27001 controls. Treat it as a developer-feedback tool until that work is done.
- **Not multi-tenant.** One instance serves one workspace. For multiple unrelated customers, run separate instances.
- **Single maintainer.** Bug reports get fast turnaround on weekdays; weekends are slower. Factor this in if you're an enterprise evaluating this.
- **Cursor agent / Copilot CLI are partially out of scope.** Requests route through proprietary backends (`api.cursor.sh`, `api.githubcopilot.com`). You get editor-level capture; you don't get the prompt or model.

---

## Roadmap

In rough priority order:

- **Tier 2 adapters** — Aider (git-log fingerprinting), Cline / Roo Code (XML tool format), Continue's text-fallback path
- **CLI wrapper** — `lineagelens run -- <ai-tool> "..."` for capture without a full proxy setup
- **Risk scoring v2** — heuristic + lightweight static analysis, closer to "this file is sensitive" detection
- **Dedicated docs site** — current docs are in `lineagelens-docs/`; a navigable site is overdue

Not on the roadmap (yet): hosted SaaS, Cursor/Windsurf full agent capture, AIBOM compliance certification.

---

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for setup instructions and contribution guidelines.

If something doesn't work, open an issue — hard to fix what I don't know is broken. Security issues go to [SECURITY.md](SECURITY.md), not public issues.

---

<div align="center">

**If LineageLens saved you 30 minutes of "wait, which AI wrote this?" — consider giving it a star.**

[![Star this repo](https://img.shields.io/github/stars/karnati-praveen/lineagelens?style=for-the-badge&color=f9a825&logo=github&label=Star%20LineageLens)](https://github.com/karnati-praveen/lineagelens/stargazers)

<br/>

Built by [Karnati Praveen](https://github.com/karnati-praveen) · MIT License · [lineage-website.vercel.app](https://lineage-website.vercel.app/)

</div>

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0d1117,40:1a1a3e,100:0d1117&height=120&section=footer" alt="footer" />
