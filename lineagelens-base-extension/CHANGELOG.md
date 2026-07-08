# Changelog

## [1.3.3] - 2026-06-26

### Added
- **Welcome / getting-started panel** (`LineageLens: Open Welcome`, also shown on first run and from
  the empty sidebar): a single page showcasing every feature with one-click "try it" buttons, plus a
  clear **Easy Mode → Power Mode** comparison and step-by-step instructions for starting the proxy.

## [1.3.2] - 2026-06-26

### Changed
- **Redesigned the AI Captures list** into a clean, compact, scannable layout: filename is the
  anchor, source is a thin colored left rail, review and risk show as small badges, AI confidence is
  a quiet dot indicator, and the code preview + actions reveal on hover — far less visual noise.

## [1.3.1] - 2026-06-26

### Added
- **Sidebar actions bar**: the trust-layer commands (Verify, Capsule, PR summary, Pre-commit,
  Timeline, Focus, Export JSON / Agent Trace) are now one-click buttons in the AI Captures panel,
  not only in the command palette.

## [1.3.0] - 2026-06-25

### Added — IDE-native trust layer
- **CodeLens, hover receipts, and gutter decorations** on captured AI ranges that follow the code
  as it moves (original / modified / moved / deleted) and show review + risk state.
- **AI Timeline** per file (`LineageLens: Show AI Timeline for This File`) and a **focus mode**
  (`LineageLens: Toggle AI Focus Mode`).
- **Review workflow**: mark captures reviewed / needs-changes / rejected with notes, a risk-tailored
  review checklist, a sidebar review chip, a **pre-commit gate**
  (`LineageLens: Check AI Changes Before Commit`), and a deterministic **PR summary**
  (`LineageLens: Generate PR Summary of AI Changes`).
- **Evidence layer**: a tamper-evident hash chain over each capture's immutable facts, best-effort
  git branch/commit binding, an **offline verifier** (`LineageLens: Verify Local Evidence Store`),
  and a verifiable **evidence capsule** export (`LineageLens: Export Evidence Capsule`).
- **Local risk signals** (auth, crypto, SQL, shell, eval, dependencies, CI, infra, secrets,
  security-bypass, untested logic) surfaced in CodeLens, hover, decorations, the receipt, the
  pre-commit gate, and the PR summary. Heuristic signals, not a security scan.
- **Recall** of similar captured blocks (`⟲ Recall similar`) with an exportable report, and an
  **AI instruction-file influence** note (`.cursorrules`, `copilot-instructions.md`, …) in the receipt.
- **Backend bridge**: capability detection that sends a richer, lossless evidence payload when the
  backend supports it, falling back to the legacy file-level payload otherwise.

### Changed
- Capture records gain a versioned evidence layer (schema v2); older stores migrate automatically.
- Capture-store persistence is now awaitable and resilient to transient/teardown filesystem errors.

## [1.2.3] - 2026-06-04

### Added
- `LineageLens: Export Agent Trace` command — export captures as portable cursor/agent-trace
  0.1.0 JSONL with no backend required (Easy Mode).

## [1.2.2]

### Added
- Always-visible status-bar trash button to clear all captures.
- Single-click a capture to insert its code at the active editor cursor.

### Changed
- Removed the unreliable tree-header action approach in favour of the status-bar button.

## [1.2.0]

### Added
- Easy/Power mode indicator in the status bar with automatic proxy detection (polls
  `/proxy-health` every 30 s; switches to Power Mode with no restart).
- Optional backend sync for Easy Mode via `lineagelensBase.backendUrl`, `ingestToken`, and
  `workspaceId` settings — file-level captures POSTed to a LineageLens backend.
- Drag-and-drop: reorder captures within the sidebar and drag a capture into any editor to insert it.
- Always-visible "Clear all captures" row pinned to the top of the sidebar.
- Right-click context actions: **Insert at Cursor** and **Copy Code**.

## [1.0.0] - 2025-05-17

### Added
- AI insertion capture via VS Code file change detection
- Sidebar panel showing all captured AI insertions with timestamp, model, and lines added
- Per-capture detail view with full inserted code, language, file path, and metadata
- Export all captures as JSON
- Configurable minimum insertion line threshold (default: 4 lines)
- Configurable exclude patterns for node_modules, .git, dist
- Maximum stored captures limit with automatic pruning
- Enable/disable capture toggle from settings
