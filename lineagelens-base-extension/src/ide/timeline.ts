/**
 * Per-file AI timeline — a chronological view of every capture recorded for the
 * active file, with each block's current lineage and review state. Lighter than
 * a full semantic diff; answers "how did AI code get into this file, and what
 * happened to it since?"
 */

import * as vscode from 'vscode';
import { CaptureRecord, CaptureStore, LineageState } from '../store';
import { locateCapture } from '../evidence/rangeBinding';
import { sourceLabel } from './labels';

interface TimelineItem {
  record: CaptureRecord;
  lineage: LineageState;
}

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < 24; i++) { out += chars.charAt(Math.floor(Math.random() * chars.length)); }
  return out;
}

function lineageLabel(state: LineageState): string {
  switch (state) {
    case 'original': return 'unchanged since capture';
    case 'modified': return 'modified since capture';
    case 'moved': return 'moved since capture';
    case 'deleted': return 'removed from file';
    default: return 'position unknown';
  }
}

function reviewLabel(state: CaptureRecord['reviewState']): string {
  switch (state) {
    case 'reviewed': return 'reviewed';
    case 'needs_changes': return 'needs changes';
    case 'rejected': return 'rejected';
    case 'accepted': return 'accepted';
    default: return 'unreviewed';
  }
}

/** Open (or reveal) the AI timeline webview for the active editor's file. */
export function openFileTimeline(store: CaptureStore, context: vscode.ExtensionContext): void {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage('LineageLens: open a file to see its AI timeline.');
    return;
  }

  const filePath = editor.document.uri.fsPath;
  const text = editor.document.getText();
  const fileName = filePath.split(/[\\/]/).filter(Boolean).pop() || filePath;

  const items: TimelineItem[] = store
    .getAll()
    .filter((r) => r.filePath === filePath)
    .map((r) => ({ record: r, lineage: locateCapture(text, r).lineageState }))
    // Oldest first — read the file's AI history top-to-bottom.
    .sort((a, b) => a.record.timestamp.localeCompare(b.record.timestamp));

  if (items.length === 0) {
    vscode.window.showInformationMessage(`LineageLens: no AI captures recorded for ${fileName}.`);
    return;
  }

  const panel = vscode.window.createWebviewPanel(
    'lineagelens.timeline',
    `AI Timeline: ${fileName}`,
    vscode.ViewColumn.Beside,
    { enableScripts: true },
  );
  panel.webview.html = timelineHtml(panel.webview, fileName, items);

  panel.webview.onDidReceiveMessage(
    (msg: { type: string; id?: string }) => {
      if (msg.type === 'open' && msg.id) {
        vscode.commands.executeCommand('lineagelens.openCapture', msg.id);
      }
    },
    undefined,
    context.subscriptions,
  );
}

function timelineHtml(webview: vscode.Webview, fileName: string, items: TimelineItem[]): string {
  const nonce = getNonce();
  const csp = [
    `default-src 'none'`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
  ].join('; ');

  const rows = items
    .map((it) => {
      const r = it.record;
      const when = (() => {
        try { return new Date(r.timestamp).toLocaleString(); } catch { return r.timestamp; }
      })();
      const drift = it.lineage === 'modified' || it.lineage === 'moved' || it.lineage === 'deleted';
      return `<li class="item ${drift ? 'drift' : ''}" data-id="${esc(r.id)}">
        <div class="dot"></div>
        <div class="body">
          <div class="row1"><span class="src">${esc(sourceLabel(r.source))}</span>
            <span class="lines">+${r.linesAdded}</span>
            <span class="when">${esc(when)}</span></div>
          <div class="row2"><span class="badge review-${esc(r.reviewState ?? 'unreviewed')}">${esc(reviewLabel(r.reviewState))}</span>
            <span class="badge lineage-${esc(it.lineage)}">${esc(lineageLabel(it.lineage))}</span></div>
        </div>
      </li>`;
    })
    .join('');

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>AI Timeline</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: var(--vscode-font-family); font-size: 12px; color: var(--vscode-foreground);
    background: var(--vscode-editor-background); padding: 16px 18px; }
  h1 { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
  .sub { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 16px; }
  ul { list-style: none; border-left: 2px solid var(--vscode-panel-border); margin-left: 6px; }
  .item { position: relative; padding: 6px 0 14px 18px; cursor: pointer; }
  .item .dot { position: absolute; left: -7px; top: 9px; width: 10px; height: 10px; border-radius: 50%;
    background: var(--vscode-descriptionForeground); border: 2px solid var(--vscode-editor-background); }
  .item.drift .dot { background: #f5a623; }
  .item:hover .body { background: var(--vscode-list-hoverBackground); border-radius: 6px; }
  .body { padding: 4px 8px; }
  .row1 { display: flex; align-items: center; gap: 8px; }
  .src { font-weight: 600; }
  .lines { color: #34d058; font-weight: 600; }
  .when { margin-left: auto; color: var(--vscode-descriptionForeground); font-size: 11px; }
  .row2 { margin-top: 4px; display: flex; gap: 6px; }
  .badge { font-size: 10px; padding: 1px 7px; border-radius: 10px; border: 1px solid var(--vscode-panel-border);
    color: var(--vscode-descriptionForeground); }
  .review-reviewed, .review-accepted { color: #34d058; border-color: rgba(52,208,88,.4); }
  .review-needs_changes, .review-rejected { color: #f44747; border-color: rgba(244,71,71,.4); }
  .lineage-modified, .lineage-moved, .lineage-deleted { color: #f5a623; border-color: rgba(245,166,35,.4); }
</style>
</head>
<body>
  <h1>AI Timeline — ${esc(fileName)}</h1>
  <div class="sub">${items.length} capture${items.length === 1 ? '' : 's'} recorded for this file · click any entry for its receipt</div>
  <ul>${rows}</ul>
  <script nonce="${nonce}">
    (function () {
      const vscode = acquireVsCodeApi();
      document.querySelectorAll('.item').forEach(function (el) {
        el.addEventListener('click', function () {
          vscode.postMessage({ type: 'open', id: el.getAttribute('data-id') });
        });
      });
    })();
  </script>
</body>
</html>`;
}
