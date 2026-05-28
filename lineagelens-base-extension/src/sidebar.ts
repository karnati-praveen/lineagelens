import * as vscode from 'vscode';
import { CaptureRecord, CaptureStore } from './store';

/** Custom MIME type used to carry capture IDs across the drag-and-drop boundary. */
export const CAPTURE_DRAG_MIME = 'application/vnd.lineagelens.capture';

// Icon codename + VS Code chart color per language.
// ThemeColor('charts.*') are the palette colors designed to be visually distinct
// across both light and dark themes.
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
  if (!isoString || typeof isoString !== 'string') { return 'unknown'; }
  const then = new Date(isoString).getTime();
  if (isNaN(then)) { return 'unknown'; }
  const now = Date.now();
  const diff = Math.floor((now - then) / 1000);
  if (diff < -60) { return 'unknown'; }  // clock skew — timestamp is in the future
  if (diff < 60) { return 'just now'; }
  if (diff < 3600) { return `${Math.floor(diff / 60)}m ago`; }
  if (diff < 86400) { return `${Math.floor(diff / 3600)}h ago`; }
  if (diff < 172800) { return 'yesterday'; }
  return new Date(isoString).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export class CaptureTreeItem extends vscode.TreeItem {
  constructor(public readonly record: CaptureRecord) {
    super(record.fileName, vscode.TreeItemCollapsibleState.None);

    // Secondary text: relative time + line count
    this.description = `${relativeTime(record.timestamp)}  +${record.linesAdded}`;

    // Hover tooltip — rich markdown with a code preview
    const preview = record.insertedCode.slice(0, 400);
    const truncated = record.insertedCode.length > 400 ? '\n…' : '';
    const tip = new vscode.MarkdownString();
    tip.isTrusted = true;
    tip.appendMarkdown(`**$(file-code) ${record.fileName}**\n\n`);
    tip.appendMarkdown(`\`${record.language}\` · \`+${record.linesAdded} lines\`\n\n`);
    tip.appendCodeblock(preview + truncated, record.language);
    if (record.workspaceFolder) {
      tip.appendMarkdown(`\n*${record.workspaceFolder}*`);
    }
    this.tooltip = tip;

    // Colored language icon
    this.iconPath = langIcon(record.language);

    // contextValue drives the "when" clause in package.json menus
    this.contextValue = 'captureRecord';

    // Single-click → open detail panel (arguments uses the id string so the
    // command handler gets a plain string, avoiding the context-menu ambiguity)
    this.command = {
      command: 'lineagelens.openCapture',
      title: 'View Capture',
      arguments: [record.id],
    };
  }
}

export class CaptureTreeProvider
  implements
    vscode.TreeDataProvider<CaptureTreeItem>,
    vscode.TreeDragAndDropController<CaptureTreeItem>,
    vscode.Disposable
{
  private _onDidChangeTreeData = new vscode.EventEmitter<CaptureTreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  // ── TreeDragAndDropController ─────────────────────────────────────────────
  // dragMimeTypes  — what this controller advertises when a drag starts
  // dropMimeTypes  — what this controller accepts when something is dropped on it
  readonly dragMimeTypes: readonly string[] = [CAPTURE_DRAG_MIME];
  readonly dropMimeTypes: readonly string[] = [CAPTURE_DRAG_MIME];

  constructor(private store: CaptureStore) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: CaptureTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(): CaptureTreeItem[] {
    return this.store.getAll().map(r => new CaptureTreeItem(r));
  }

  /**
   * Called by VS Code when the user begins dragging one or more tree items.
   * We publish:
   *   - our custom MIME with a comma-separated list of capture IDs (used for
   *     both in-tree reordering and editor drop insertion)
   */
  handleDrag(
    source: readonly CaptureTreeItem[],
    dataTransfer: vscode.DataTransfer,
    _token: vscode.CancellationToken,
  ): void {
    // Custom MIME carries IDs — used by handleDrop (in-tree reorder) and
    // the DocumentDropEditProvider registered in extension.ts.
    const ids = source.map(item => item.record.id).join(',');
    dataTransfer.set(CAPTURE_DRAG_MIME, new vscode.DataTransferItem(ids));

    // text/plain is the universal drop target: VS Code inserts this verbatim
    // when the user drops the item onto any open editor, no extra confirmation
    // needed.  Multiple captures are separated by a comment divider.
    const separator = '\n\n// ── AI capture ──────────────────────\n\n';
    const code = source.map(item => item.record.insertedCode).join(separator);
    dataTransfer.set('text/plain', new vscode.DataTransferItem(code));
  }

  /**
   * Called by VS Code when items are dropped onto the tree itself.
   * Reorders the dropped captures so they appear immediately before the
   * target item (or at the bottom when dropped on empty space).
   */
  async handleDrop(
    target: CaptureTreeItem | undefined,
    dataTransfer: vscode.DataTransfer,
    _token: vscode.CancellationToken,
  ): Promise<void> {
    const item = dataTransfer.get(CAPTURE_DRAG_MIME);
    if (!item) { return; }
    const ids = (await item.asString()).split(',').filter(Boolean);
    this.store.reorder(ids, target?.record.id);
    this.refresh();
  }

  dispose(): void {
    this._onDidChangeTreeData.dispose();
  }
}

export function buildDetailPanel(panel: vscode.WebviewPanel, record: CaptureRecord): void {
  const date = new Date(record.timestamp).toLocaleString();
  const esc = (s: string) =>
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
    width: 36px;
    height: 36px;
    border-radius: 8px;
    background: var(--vscode-badge-background);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
  }
  .header-title { font-size: 15px; font-weight: 600; }
  .header-sub { font-size: 11px; color: var(--vscode-descriptionForeground); margin-top: 2px; }
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
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 4px;
  }
  .card-value { font-size: 13px; font-weight: 500; }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    color: #fff;
    background: ${langColor};
  }
  .lines-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    background: rgba(34,197,94,.15);
    color: #22c55e;
    border: 1px solid rgba(34,197,94,.3);
  }
  .code-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .code-label { font-size: 12px; font-weight: 600; color: var(--vscode-descriptionForeground); }
  .copy-btn {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
    border: none;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 11px;
    cursor: pointer;
    font-family: var(--vscode-font-family);
  }
  .copy-btn:hover { background: var(--vscode-button-secondaryHoverBackground); }
  pre {
    background: var(--vscode-textCodeBlock-background);
    border: 1px solid var(--vscode-panel-border);
    border-radius: 8px;
    padding: 16px;
    overflow-x: auto;
    font-size: 12px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .path {
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 11px;
    color: var(--vscode-descriptionForeground);
    word-break: break-all;
  }
  .id {
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 10px;
    color: var(--vscode-descriptionForeground);
    opacity: .6;
  }
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
    ${record.workspaceFolder ? `
    <div class="card" style="grid-column:1/-1">
      <div class="card-label">Workspace</div>
      <div class="card-value">${esc(record.workspaceFolder)}</div>
    </div>` : ''}
  </div>

  <div class="code-header">
    <span class="code-label">Inserted Code</span>
    <button class="copy-btn" id="copyBtn" type="button">Copy</button>
  </div>
  <pre><code id="code">${esc(record.insertedCode)}</code></pre>

  <div style="margin-top:14px">
    <span class="id">ID: ${esc(record.id)}</span>
  </div>

  <script>
    (function() {
      var btn = document.getElementById('copyBtn');
      var codeEl = document.getElementById('code');
      if (!btn || !codeEl) { return; }
      btn.addEventListener('click', function() {
        var text = codeEl.textContent || '';
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function() {
            btn.textContent = 'Copied!';
            setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
          }).catch(function() {
            btn.textContent = 'Copy failed';
            setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
          });
        } else {
          btn.textContent = 'Copy unsupported';
        }
      });
    })();
  </script>
</body>
</html>`;
}
