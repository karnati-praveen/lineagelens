# Contributing to LineageLens

Thanks for taking the time to contribute. LineageLens is a single-maintainer open source project and every PR, issue, and idea genuinely helps.

## Quick links

- [Report a bug](.github/ISSUE_TEMPLATE/bug_report.yml)
- [Request a feature](.github/ISSUE_TEMPLATE/feature_request.yml)
- [Security vulnerabilities](../SECURITY.md) — please do not file public issues for these

---

## How to contribute

### Reporting bugs

Use the **Bug report** issue template. The more detail you provide (tier, OS, tool, logs), the faster it gets fixed.

### Suggesting features

Use the **Feature request** issue template. Start with the problem you're solving, not the solution. If you're proposing a new adapter, include the tool name and how it routes API calls.

### Opening a pull request

1. Fork the repo and create a branch: `git checkout -b your-feature`
2. Follow the setup instructions for your component (see below)
3. Make your change. Keep it focused — one PR, one concern.
4. Run tests before pushing
5. Open a PR against `main` with the template filled out

---

## Local dev setup

### VS Code extension

```bash
cd lineagelens-src
npm install
npm run compile
# Press F5 in VS Code to open the Extension Development Host
```

Tests:
```bash
npm test
```

### Backend (FastAPI)

```bash
cd lineagelens-backend
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload --port 8787
```

Tests:
```bash
pytest tests/
```

### Proxy

```bash
cd lineagelens-proxy
pip install -r requirements.txt
python proxy_server.py
```

### Lite stack (all-in-one via Docker)

```bash
bash lineagelens-scripts/quickstart-lite.sh
```

---

## Code style

- **TypeScript (extension):** standard tsc settings in `tsconfig.json`. No eslint config yet — use common sense.
- **Python (backend/proxy):** Black-formatted, ruff for linting. Run `black . && ruff check .` before pushing.
- **No required commit format** — just be descriptive.

---

## Adapter contributions

Adding support for a new AI coding tool is one of the highest-value contributions. Adapters live in `lineagelens-src/agentAdapters/`. Each adapter must export:

```ts
{
  toolName: string,
  detect(): boolean,         // Is this tool active in the current workspace?
  capture(): AdapterResult,  // Extract prompt, response, model, sessionId
}
```

See `claudeCodeAdapter.ts` for a reference implementation. Open an issue before building so we can coordinate on the API traffic format.

---

## What we're not looking for (right now)

- SaaS hosting / cloud deployment layer
- Authentication overhaul
- UI redesigns without prior discussion
- New database backends beyond SQLite / PostgreSQL / Neo4j

If in doubt, open an issue first to discuss.

---

## License

By contributing you agree that your contributions will be licensed under the [MIT License](../LICENSE).
