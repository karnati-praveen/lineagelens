import * as vscode from 'vscode';
import { randomBytes } from 'node:crypto';
import type { ProvenanceSearchResultItem, ProvenanceStorageService } from './storage/StorageService';

const DEFAULT_LINE_THRESHOLD = 4;

type TraceRecord = {
  uuid: string;
  filePath: string | null;
  cursorLine: number | null;
  model: string | null;
  timestampIso: string;
  insertedCode: string;
  promptMessages: unknown;
  sessionId: string | null;
  developer: string | null;
};

function extractSessionId(record: Record<string, unknown> | undefined): string | null {
  if (!record) return null;
  const payload = record['normalizedEvent'] as Record<string, unknown> | undefined;
  if (payload?.['session'] && typeof payload['session'] === 'object') {
    const s = payload['session'] as Record<string, unknown>;
    return (s['sessionId'] as string | null) ?? null;
  }
  return (record['sessionId'] as string | null) ?? null;
}

function extractDeveloper(record: Record<string, unknown> | undefined): string | null {
  if (!record) return null;
  const snap = record['contextSnapshot'] as Record<string, unknown> | undefined;
  if (snap) {
    return (snap['gitUser'] as string | null) ?? (snap['username'] as string | null) ?? null;
  }
  return null;
}

function toTraceRecord(item: ProvenanceSearchResultItem): TraceRecord {
  const fullRecord = item.record;
  const cursorLine = fullRecord
    ? ((fullRecord['cursorLine'] as number | null) ??
       (fullRecord['insertion'] as Record<string, unknown> | undefined)?.['cursorPosition'] != null
         ? ((fullRecord['insertion'] as Record<string, unknown>)['cursorPosition'] as Record<string, unknown>)?.['line'] as number | null
         : null)
    : null;

  return {
    uuid: item.uuid,
    filePath: item.filePath,
    cursorLine: typeof cursorLine === 'number' ? cursorLine : null,
    model: item.model,
    timestampIso: item.timestampIso ?? '',
    insertedCode: item.snippet,
    promptMessages: fullRecord?.['promptMessages'] ?? fullRecord?.['prompt'] ?? null,
    sessionId: extractSessionId(fullRecord),
    developer: extractDeveloper(fullRecord)
  };
}

function renderPrompt(promptMessages: unknown): string {
  if (!promptMessages) return '<em>No prompt captured</em>';
  if (typeof promptMessages === 'string') return escHtml(promptMessages);

  if (Array.isArray(promptMessages)) {
    return promptMessages
      .map((m: unknown) => {
        if (!m || typeof m !== 'object') return '';
        const msg = m as Record<string, unknown>;
        const role = String(msg['role'] ?? 'user');
        const content =
          typeof msg['content'] === 'string'
            ? escHtml(msg['content'])
            : escHtml(JSON.stringify(msg['content']));
        return `<div class="msg-block"><span class="role-pill">${escHtml(role)}</span><pre class="msg-content">${content}</pre></div>`;
      })
      .join('');
  }

  return `<pre>${escHtml(JSON.stringify(promptMessages, null, 2))}</pre>`;
}

function escHtml(s: string): string {
  return s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function createNonce(): string {
  // CSP nonce must be cryptographically random — Math.random() is predictable
  // and would let an attacker bypass the CSP by guessing the nonce.
  return randomBytes(16).toString('hex');
}

function buildSessionGraphHtml(sessionRecords: TraceRecord[], currentUuid: string): string {
  if (sessionRecords.length === 0) return '<p class="empty">No other insertions found in this session.</p>';

  const items = sessionRecords.map((rec) => {
    const active = rec.uuid === currentUuid ? ' class="session-item active"' : ' class="session-item"';
    const lineLabel = rec.cursorLine != null ? `:${rec.cursorLine + 1}` : '';
    const fileLabel = rec.filePath ? rec.filePath.split(/[\\/]/).pop() ?? rec.filePath : '(unknown)';
    const ts = rec.timestampIso ? new Date(rec.timestampIso).toLocaleString() : '';

    return `<div${active} data-uuid="${escHtml(rec.uuid)}" data-filepath="${escHtml(rec.filePath ?? '')}" data-line="${rec.cursorLine ?? 0}">
  <span class="session-file">${escHtml(fileLabel)}${escHtml(lineLabel)}</span>
  <span class="session-ts">${escHtml(ts)}</span>
  <span class="session-model">${escHtml(rec.model ?? '')}</span>
  <button class="jump-btn" data-filepath="${escHtml(rec.filePath ?? '')}" data-line="${rec.cursorLine ?? 0}">Jump</button>
</div>`;
  });

  return `<div class="session-graph">${items.join('\n')}</div>`;
}

function buildPanelHtml(
  webview: vscode.Webview,
  record: TraceRecord | null,
  filePath: string,
  line: number,
  sessionRecords: TraceRecord[]
): string {
  const nonce = createNonce();
  const lineDisplay = line + 1;

  if (!record) {
    return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';"/>
<title>Trace Line</title>
<style>body{color:var(--vscode-editor-foreground);background:var(--vscode-editor-background);font-family:var(--vscode-font-family);padding:16px;}</style>
</head><body>
<h2>Trace: ${escHtml(filePath.split(/[\\/]/).pop() ?? filePath)} line ${lineDisplay}</h2>
<p>No AI provenance record found for this line.</p>
<p style="color:var(--vscode-descriptionForeground);font-size:12px;">LineageLens records insertions of ${DEFAULT_LINE_THRESHOLD}+ net added lines. If this line was part of a smaller change or typed manually, no record exists.</p>
</body></html>`;
  }

  const promptHtml = renderPrompt(record.promptMessages);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Trace Line</title>
  <style>
    :root { color-scheme: light dark; }
    body {
      margin: 0; padding: 14px;
      color: var(--vscode-editor-foreground);
      background: var(--vscode-editor-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      line-height: 1.5;
    }
    h2 { margin: 0 0 4px; font-size: 14px; font-weight: 700; }
    .subtitle { font-size: 11px; color: var(--vscode-descriptionForeground); margin-bottom: 14px; }
    .section { margin-bottom: 14px; }
    .section-title {
      font-size: 11px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--vscode-descriptionForeground);
      margin-bottom: 6px;
    }
    .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
    .meta-item { background: color-mix(in srgb, var(--vscode-editor-background) 88%, var(--vscode-panel-border));
      border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 7px; }
    .meta-item .label { font-size: 10px; color: var(--vscode-descriptionForeground); }
    .meta-item .val { font-size: 13px; font-weight: 600; margin-top: 2px; word-break: break-all; }
    .prompt-box {
      background: color-mix(in srgb, var(--vscode-editor-background) 88%, var(--vscode-panel-border));
      border: 1px solid var(--vscode-panel-border); border-radius: 5px; padding: 10px;
      max-height: 200px; overflow-y: auto;
    }
    .msg-block { margin-bottom: 8px; }
    .role-pill {
      display: inline-block; padding: 1px 7px; border-radius: 999px;
      font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
      border: 1px solid var(--vscode-panel-border); margin-bottom: 3px;
    }
    .msg-content {
      margin: 0; white-space: pre-wrap; word-break: break-word;
      font-size: 11px; font-family: var(--vscode-editor-font-family, monospace);
    }
    pre { margin: 0; white-space: pre-wrap; word-break: break-word;
      font-family: var(--vscode-editor-font-family, monospace); font-size: 11px; }
    .code-box {
      background: var(--vscode-editor-background);
      border: 1px solid var(--vscode-panel-border); border-radius: 5px;
      padding: 8px; max-height: 160px; overflow-y: auto;
      font-family: var(--vscode-editor-font-family, monospace); font-size: 11px;
      white-space: pre-wrap; word-break: break-word;
    }
    .session-graph { display: flex; flex-direction: column; gap: 4px; }
    .session-item {
      display: flex; align-items: center; gap: 6px;
      padding: 5px 8px; border-radius: 5px;
      border: 1px solid var(--vscode-panel-border);
      background: color-mix(in srgb, var(--vscode-editor-background) 92%, var(--vscode-panel-border));
    }
    .session-item.active { border-color: var(--vscode-focusBorder); background: color-mix(in srgb, var(--vscode-focusBorder) 12%, transparent); }
    .session-file { font-weight: 600; font-size: 12px; flex: 1; word-break: break-all; }
    .session-ts { font-size: 10px; color: var(--vscode-descriptionForeground); white-space: nowrap; }
    .session-model { font-size: 10px; color: var(--vscode-descriptionForeground); white-space: nowrap; }
    .jump-btn {
      font-size: 10px; padding: 2px 8px; cursor: pointer;
      border: 1px solid var(--vscode-button-background);
      background: var(--vscode-button-background); color: var(--vscode-button-foreground);
      border-radius: 4px;
    }
    .jump-btn:hover { opacity: 0.85; }
    .empty { color: var(--vscode-descriptionForeground); font-size: 12px; }
  </style>
</head>
<body>
  <h2>Trace: ${escHtml(filePath.split(/[\\/]/).pop() ?? filePath)} line ${lineDisplay}</h2>
  <div class="subtitle">UUID: ${escHtml(record.uuid)}</div>

  <div class="section">
    <div class="section-title">Attribution</div>
    <div class="meta-grid">
      <div class="meta-item"><div class="label">Model</div><div class="val">${escHtml(record.model ?? '—')}</div></div>
      <div class="meta-item"><div class="label">Developer</div><div class="val">${escHtml(record.developer ?? '—')}</div></div>
      <div class="meta-item"><div class="label">Timestamp</div><div class="val">${escHtml(record.timestampIso ? new Date(record.timestampIso).toLocaleString() : '—')}</div></div>
      <div class="meta-item"><div class="label">Session ID</div><div class="val">${escHtml(record.sessionId ? record.sessionId.slice(0, 16) + '…' : '—')}</div></div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Prompt</div>
    <div class="prompt-box">${promptHtml}</div>
  </div>

  <div class="section">
    <div class="section-title">Inserted Code</div>
    <div class="code-box">${escHtml(record.insertedCode)}</div>
  </div>

  <div class="section">
    <div class="section-title">Session Graph (${sessionRecords.length} insertion${sessionRecords.length !== 1 ? 's' : ''})</div>
    ${buildSessionGraphHtml(sessionRecords, record.uuid)}
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    document.querySelectorAll('.jump-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        vscode.postMessage({
          type: 'jumpToLine',
          filePath: btn.dataset.filepath,
          line: parseInt(btn.dataset.line || '0', 10)
        });
      });
    });
  </script>
</body>
</html>`;
}

export class TraceLinePanelManager {
  private static panelMap = new Map<string, vscode.WebviewPanel>();

  public static async show(
    filePath: string,
    line: number,
    storageService: ProvenanceStorageService,
    extensionUri: vscode.Uri,
    log: (msg: string) => void
  ): Promise<void> {
    const panelKey = `${filePath}:${line}`;
    const existingPanel = TraceLinePanelManager.panelMap.get(panelKey);
    if (existingPanel) {
      existingPanel.reveal();
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      'lineagelens.traceLine',
      `Trace: line ${line + 1}`,
      vscode.ViewColumn.Beside,
      { enableScripts: true, localResourceRoots: [extensionUri] }
    );

    TraceLinePanelManager.panelMap.set(panelKey, panel);
    panel.onDidDispose(() => TraceLinePanelManager.panelMap.delete(panelKey));

    panel.webview.html = buildPanelHtml(panel.webview, null, filePath, line, []);

    try {
      const results = await storageService.search(
        {
          keywords: '',
          model: '',
          dateFrom: '',
          dateTo: '',
          currentFileOnly: true,
          currentFilePath: filePath,
          limit: 200
        },
        vscode.Uri.file(filePath)
      );

      // Exclude any records that don't belong to the clicked file — the backend
      // filter is best-effort and may include nearby paths on some storage backends.
      const normalizedFilePath = filePath.replaceAll('\\', '/').toLowerCase();
      const fileRecords = results
        .map((r) => toTraceRecord(r))
        .filter((r) => {
          if (!r.filePath) return false;
          return r.filePath.replaceAll('\\', '/').toLowerCase() === normalizedFilePath;
        });

      const match = findBestMatch(fileRecords, line);
      if (!match) {
        panel.webview.html = buildPanelHtml(panel.webview, null, filePath, line, []);
        return;
      }

      const sessionRecords = match.sessionId
        ? fileRecords.filter((r) => r.sessionId === match.sessionId)
        : [match];

      panel.webview.html = buildPanelHtml(panel.webview, match, filePath, line, sessionRecords);

      const messageListener = panel.webview.onDidReceiveMessage(async (msg: unknown) => {
        const m = msg as Record<string, unknown>;
        if (m['type'] === 'jumpToLine') {
          const fp = m['filePath'] as string | undefined;
          const ln = (m['line'] as number | undefined) ?? 0;
          if (!fp) return;
          try {
            const uri = vscode.Uri.file(fp);
            const doc = await vscode.workspace.openTextDocument(uri);
            const editor = await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
            const position = new vscode.Position(ln, 0);
            editor.selection = new vscode.Selection(position, position);
            editor.revealRange(new vscode.Range(position, position), vscode.TextEditorRevealType.InCenter);
          } catch (err) {
            log('TraceLinePanel: failed to jump to ' + fp + ':' + ln + ' — ' + String(err));
          }
        }
      });
      panel.onDidDispose(() => messageListener.dispose());
    } catch (err) {
      log('TraceLinePanel: search failed — ' + String(err));
      panel.webview.html = buildPanelHtml(panel.webview, null, filePath, line, []);
    }
  }
}

function findBestMatch(records: TraceRecord[], targetLine: number): TraceRecord | null {
  if (records.length === 0) return null;

  let best: TraceRecord | null = null;
  let bestDist = Infinity;

  for (const rec of records) {
    if (rec.cursorLine == null) continue;
    const dist = Math.abs(rec.cursorLine - targetLine);
    if (dist < bestDist) {
      bestDist = dist;
      best = rec;
    }
  }

  if (best && bestDist <= 100) return best;
  return null;
}
