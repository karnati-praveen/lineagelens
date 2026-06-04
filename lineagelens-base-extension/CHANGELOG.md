# Changelog

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
