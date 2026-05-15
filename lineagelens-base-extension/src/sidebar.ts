import * as vscode from 'vscode';
import { CaptureRecord, CaptureStore } from './store';

export class CaptureTreeItem extends vscode.TreeItem {
  constructor(public readonly record: CaptureRecord) {
    super(record.fileName, vscode.TreeItemCollapsibleState.None);
    const date = new Date(record.timestamp);
    const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const dateStr = date.toLocaleDateString([], { month: 'short', day: 'numeric' });
    this.description = `${dateStr} ${timeStr} · +${record.linesAdded} lines`;
    this.tooltip = new vscode.MarkdownString(
      `**${record.fileName}**\n\n` +
      `- Language: \`${record.language}\`\n` +
      `- Lines added: ${record.linesAdded}\n` +
      `- Captured: ${date.toLocaleString()}\n\n` +
      `\`\`\`${record.language}\n${record.insertedCode.slice(0, 300)}${record.insertedCode.length > 300 ? '\n…' : ''}\n\`\`\``
    );
    this.iconPath = new vscode.ThemeIcon('symbol-event');
    this.contextValue = 'captureRecord';
    this.command = {
      command: 'lineagelens.openCapture',
      title: 'View Capture',
      arguments: [record.id],
    };
  }
}

export class CaptureTreeProvider implements vscode.TreeDataProvider<CaptureTreeItem> {
  private _onDidChangeTreeData = new vscode.EventEmitter<CaptureTreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

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
}

export function buildDetailPanel(
  panel: vscode.WebviewPanel,
  record: CaptureRecord,
): void {
  const date = new Date(record.timestamp).toLocaleString();
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  panel.webview.html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Capture Detail</title>
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); background: var(--vscode-editor-background); padding: 20px; margin: 0; }
  h2 { font-size: 15px; font-weight: 600; margin: 0 0 16px; border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 10px; }
  .meta { display: grid; grid-template-columns: 130px 1fr; gap: 6px 12px; margin-bottom: 18px; font-size: 12px; }
  .meta-label { color: var(--vscode-descriptionForeground); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
  pre { background: var(--vscode-textCodeBlock-background); border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 14px; overflow-x: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; margin: 0; }
  .badge { display: inline-block; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); border-radius: 3px; padding: 1px 7px; font-size: 11px; font-weight: 600; }
</style>
</head>
<body>
  <h2>AI Capture — ${esc(record.fileName)}</h2>
  <div class="meta">
    <span class="meta-label">File</span><span>${esc(record.filePath)}</span>
    <span class="meta-label">Captured</span><span>${esc(date)}</span>
    <span class="meta-label">Language</span><span><span class="badge">${esc(record.language)}</span></span>
    <span class="meta-label">Lines added</span><span>+${record.linesAdded}</span>
    ${record.workspaceFolder ? `<span class="meta-label">Workspace</span><span>${esc(record.workspaceFolder)}</span>` : ''}
    <span class="meta-label">ID</span><span style="font-family:monospace;font-size:11px">${esc(record.id)}</span>
  </div>
  <pre><code>${esc(record.insertedCode)}</code></pre>
</body>
</html>`;
}
