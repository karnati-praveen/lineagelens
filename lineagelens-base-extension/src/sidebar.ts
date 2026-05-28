import * as vscode from 'vscode';
import { CaptureRecord, CaptureStore } from './store';

/** Custom MIME type for drag-and-drop between tree and editor. */
export const CAPTURE_DRAG_MIME = 'application/vnd.lineagelens.capture';

// ── Language icon + colour ────────────────────────────────────────────────────

const LANG_META: Record<string, [string, string]> = {
  typescript:       ['symbol-class',     'charts.blue'],
  typescriptreact:  ['symbol-class',     'charts.blue'],
  javascript:       ['symbol-method',    'charts.yellow'],
  javascriptreact:  ['symbol-method',    'charts.yellow'],
  python:           ['symbol-namespace', 'charts.green'],
  rust:             ['symbol-struct',    'charts.red'],
  go:               ['symbol-interface', 'charts.purple'],
  java:             ['symbol-class',     'charts.orange'],
  kotlin:           ['symbol-class',     'charts.orange'],
  cpp:              ['symbol-struct',    'charts.red'],
  c:                ['symbol-struct',    'charts.red'],
  csharp:           ['symbol-class',     'charts.purple'],
  html:             ['symbol-color',     'charts.orange'],
  css:              ['symbol-color',     'charts.blue'],
  scss:             ['symbol-color',     'charts.blue'],
  json:             ['symbol-key',       'charts.foreground'],
  yaml:             ['symbol-key',       'charts.foreground'],
  markdown:         ['book',             'charts.foreground'],
  shellscript:      ['terminal',         'charts.green'],
  bash:             ['terminal',         'charts.green'],
  powershell:       ['terminal',         'charts.blue'],
  zsh:              ['terminal',         'charts.green'],
  sql:              ['database',         'charts.yellow'],
};

function langIcon(language: string): vscode.ThemeIcon {
  const lang = (language || '').toLowerCase();
  const [icon, colorId] = LANG_META[lang] ?? ['code', 'charts.foreground'];
  return new vscode.ThemeIcon(icon, new vscode.ThemeColor(colorId));
}

function relativeTime(isoString: string): string {
  if (!isoString) { return 'unknown'; }
  const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (isNaN(diff) || diff < -60) { return 'unknown'; }
  if (diff < 60)     { return 'just now'; }
  if (diff < 3600)   { return `${Math.floor(diff / 60)}m ago`; }
  if (diff < 86400)  { return `${Math.floor(diff / 3600)}h ago`; }
  if (diff < 172800) { return 'yesterday'; }
  return new Date(isoString).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// ── Tree-item types ───────────────────────────────────────────────────────────

/**
 * Pinned header item that always sits at position 0 when captures exist.
 * Clicking it fires lineagelens.clearAll — the confirmation dialog is shown
 * by the command handler.
 */
export class ClearAllTreeItem extends vscode.TreeItem {
  static readonly CONTEXT = 'clearAllAction';

  constructor(count: number) {
    super('Clear all captures', vscode.TreeItemCollapsibleState.None);
    // Stable unique id — prevents VS Code from merging this item with cached
    // tree items from a previous extension version that didn't have it.
    this.id           = '__lineagelens_clearall__';
    this.description  = `${count} record${count !== 1 ? 's' : ''}`;
    this.iconPath     = new vscode.ThemeIcon('trash', new vscode.ThemeColor('errorForeground'));
    this.contextValue = ClearAllTreeItem.CONTEXT;
    this.tooltip      = `Permanently delete all ${count} AI captures — cannot be undone`;
    this.command = {
      command:   'lineagelens.clearAll',
      title:     'Clear All Captures',
      arguments: [],
    };
  }
}

/** One row per captured AI insertion. */
export class CaptureTreeItem extends vscode.TreeItem {
  static readonly CONTEXT = 'captureRecord';

  constructor(public readonly record: CaptureRecord) {
    super(record.fileName, vscode.TreeItemCollapsibleState.None);

    this.description = `${relativeTime(record.timestamp)}  +${record.linesAdded}`;

    const preview = record.insertedCode.slice(0, 400);
    const tip = new vscode.MarkdownString();
    tip.isTrusted = true;
    tip.appendMarkdown(`**$(file-code) ${record.fileName}**\n\n`);
    tip.appendMarkdown(`\`${record.language}\`  ·  \`+${record.linesAdded} lines\`\n\n`);
    tip.appendCodeblock(preview + (record.insertedCode.length > 400 ? '\n…' : ''), record.language);
    if (record.workspaceFolder) {
      tip.appendMarkdown(`\n\n*${record.workspaceFolder}*`);
    }
    this.tooltip      = tip;
    this.iconPath     = langIcon(record.language);
    this.contextValue = CaptureTreeItem.CONTEXT;
    // Single click = insert at cursor.  Right-click → "View Details" is still accessible.
    this.command = {
      command:   'lineagelens.insertAtCursor',
      title:     'Insert at Cursor',
      arguments: [record.id],
    };
  }
}

// ── Union type for the provider ───────────────────────────────────────────────

type TreeNode = ClearAllTreeItem | CaptureTreeItem;

// ── Tree provider ─────────────────────────────────────────────────────────────

export class CaptureTreeProvider
  implements
    vscode.TreeDataProvider<TreeNode>,
    vscode.TreeDragAndDropController<TreeNode>,
    vscode.Disposable
{
  private _onDidChangeTreeData = new vscode.EventEmitter<TreeNode | undefined | void>();
  readonly onDidChangeTreeData  = this._onDidChangeTreeData.event;

  // Only capture items can be dragged — the header is excluded in handleDrag.
  readonly dragMimeTypes: readonly string[] = [CAPTURE_DRAG_MIME];
  readonly dropMimeTypes: readonly string[] = [CAPTURE_DRAG_MIME];

  constructor(private store: CaptureStore) {}

  refresh(): void { this._onDidChangeTreeData.fire(); }

  getTreeItem(element: TreeNode): vscode.TreeItem { return element; }

  /** All items are root-level — required so VS Code can navigate via reveal(). */
  getParent(_element: TreeNode): undefined { return undefined; }

  getChildren(): TreeNode[] {
    const records = this.store.getAll();
    if (records.length === 0) { return []; }
    return [
      new ClearAllTreeItem(records.length),
      ...records.map(r => new CaptureTreeItem(r)),
    ];
  }

  // ── Drag ────────────────────────────────────────────────────────────────────

  handleDrag(
    source: readonly TreeNode[],
    dataTransfer: vscode.DataTransfer,
    _token: vscode.CancellationToken,
  ): void {
    // Ignore the header item — only capture items are draggable.
    const items = source.filter((n): n is CaptureTreeItem => n instanceof CaptureTreeItem);
    if (items.length === 0) { return; }

    const separator = '\n\n// ── AI capture ──────────────────────────────\n\n';
    const code = items.map(i => i.record.insertedCode).join(separator);

    // Custom MIME — picked up by handleDrop (reorder) and DocumentDropEditProvider (insert).
    dataTransfer.set(CAPTURE_DRAG_MIME, new vscode.DataTransferItem(
      items.map(i => i.record.id).join(','),
    ));

    // text/plain — VS Code 1.92+ inserts this directly when dropped on an editor.
    // Clipboard is intentionally NOT written here: in-tree reorder drags must not
    // clobber whatever the user had on their clipboard.
  }

  // ── Drop (in-tree reorder) ───────────────────────────────────────────────────

  async handleDrop(
    target: TreeNode | undefined,
    dataTransfer: vscode.DataTransfer,
    _token: vscode.CancellationToken,
  ): Promise<void> {
    const item = dataTransfer.get(CAPTURE_DRAG_MIME);
    if (!item) { return; }
    const ids = (await item.asString()).split(',').filter(Boolean);
    // null  → ClearAllTreeItem header → reorder to top
    // undef → empty space             → reorder to bottom
    // id    → specific capture item   → insert before it
    const targetId: string | null | undefined =
      target instanceof CaptureTreeItem ? target.record.id :
      target instanceof ClearAllTreeItem ? null :
      undefined;
    this.store.reorder(ids, targetId);
    this.refresh();
  }

  dispose(): void { this._onDidChangeTreeData.dispose(); }
}

// ── Webview detail panel ──────────────────────────────────────────────────────

export function buildDetailPanel(panel: vscode.WebviewPanel, record: CaptureRecord): void {
  const date = new Date(record.timestamp).toLocaleString();
  const esc  = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const langColors: Record<string, string> = {
    typescript: '#3178c6', javascript: '#f7df1e', python: '#3572a5',
    rust: '#dea584', go: '#00add8', java: '#b07219', cpp: '#f34b7d',
    csharp: '#178600', html: '#e34c26', css: '#563d7c', json: '#292929',
    yaml: '#cb171e', markdown: '#083fa1', shell: '#89e051', bash: '#89e051',
    sql: '#e38c00',
  };
  const langColor = langColors[record.language.toLowerCase()] ?? '#6b7280';

  panel.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';">
<title>AI Capture</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-foreground);
    background: var(--vscode-editor-background);
    padding: 20px;
    line-height: 1.5;
  }
  .header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--vscode-panel-border);
  }
  .header-icon {
    width: 36px; height: 36px; border-radius: 8px;
    background: var(--vscode-badge-background);
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
  }
  .header-title { font-size: 15px; font-weight: 600; }
  .header-sub   { font-size: 11px; color: var(--vscode-descriptionForeground); margin-top: 2px; }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
    margin-bottom: 20px;
  }
  .card {
    background: var(--vscode-textBlockQuote-background, var(--vscode-editor-inactiveSelectionBackground));
    border: 1px solid var(--vscode-panel-border);
    border-radius: 8px;
    padding: 12px 14px;
  }
  .card-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; color: var(--vscode-descriptionForeground); margin-bottom: 4px;
  }
  .card-value { font-size: 13px; font-weight: 500; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; color: #fff;
    background: ${langColor};
  }
  .lines-badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700;
    background: rgba(34,197,94,.15); color: #22c55e;
    border: 1px solid rgba(34,197,94,.3);
  }
  .code-header {
    display: flex; align-items: center;
    justify-content: space-between; margin-bottom: 8px;
  }
  .code-label { font-size: 12px; font-weight: 600; color: var(--vscode-descriptionForeground); }
  .copy-btn {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
    border: none; border-radius: 4px; padding: 4px 10px;
    font-size: 11px; cursor: pointer; font-family: var(--vscode-font-family);
  }
  .copy-btn:hover { background: var(--vscode-button-secondaryHoverBackground); }
  pre {
    background: var(--vscode-textCodeBlock-background);
    border: 1px solid var(--vscode-panel-border);
    border-radius: 8px; padding: 16px; overflow-x: auto;
    font-size: 12px; line-height: 1.6;
    white-space: pre-wrap; word-break: break-all;
  }
  .path { font-family: var(--vscode-editor-font-family, monospace); font-size: 11px; color: var(--vscode-descriptionForeground); word-break: break-all; }
  .id   { font-family: var(--vscode-editor-font-family, monospace); font-size: 10px; color: var(--vscode-descriptionForeground); opacity: .6; }
</style>
</head>
<body>
  <div class="header">
    <div class="header-icon">⚡</div>
    <div>
      <div class="header-title">AI Capture — ${esc(record.fileName)}</div>
      <div class="header-sub">${esc(date)}</div>
    </div>
  </div>
  <div class="grid">
    <div class="card">
      <div class="card-label">Language</div>
      <div class="card-value"><span class="badge">${esc(record.language)}</span></div>
    </div>
    <div class="card">
      <div class="card-label">Lines Added</div>
      <div class="card-value"><span class="lines-badge">+${record.linesAdded} lines</span></div>
    </div>
    <div class="card" style="grid-column:1/-1">
      <div class="card-label">File Path</div>
      <div class="card-value path">${esc(record.filePath)}</div>
    </div>
    ${record.workspaceFolder ? `<div class="card" style="grid-column:1/-1"><div class="card-label">Workspace</div><div class="card-value">${esc(record.workspaceFolder)}</div></div>` : ''}
  </div>
  <div class="code-header">
    <span class="code-label">Inserted Code</span>
    <button class="copy-btn" id="copyBtn" type="button">Copy</button>
  </div>
  <pre><code id="code">${esc(record.insertedCode)}</code></pre>
  <div style="margin-top:14px"><span class="id">ID: ${esc(record.id)}</span></div>
  <script>
    (function() {
      var btn = document.getElementById('copyBtn');
      var codeEl = document.getElementById('code');
      if (!btn || !codeEl) { return; }
      btn.addEventListener('click', function() {
        var text = codeEl.textContent || '';
        navigator.clipboard && navigator.clipboard.writeText(text).then(function() {
          btn.textContent = 'Copied!';
          setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
        }).catch(function() { btn.textContent = 'Copy failed'; setTimeout(function() { btn.textContent = 'Copy'; }, 1500); });
      });
    })();
  </script>
</body>
</html>`;
}
