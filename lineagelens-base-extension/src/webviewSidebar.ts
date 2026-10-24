import * as vscode from 'vscode';
import { CaptureStore, CaptureSource, ReviewState } from './store';

interface CardData {
  id: string;
  fileName: string;
  language: string;
  linesAdded: number;
  timestamp: string;
  confidence: number;
  source: CaptureSource;
  reviewState: ReviewState;
  risk: { label: string; severity: string } | null;
  filePath: string;
  workspaceFolder: string | null;
  preview: string;
}

const SIDEBAR_COMMANDS = new Set<string>([
  'lineagelens.openWelcome',
  'lineagelens.searchCaptures',
  'lineagelens.verifyEvidence',
  'lineagelens.exportCapsule',
  'lineagelens.exportPrSummary',
  'lineagelens.checkBeforeCommit',
  'lineagelens.showFileTimeline',
  'lineagelens.toggleFocusMode',
  'lineagelens.exportJson',
  'lineagelens.exportAgentTrace',
  'lineagelensBase.connectToServer',
  'lineagelensBase.disconnectServer',
]);

function firstMeaningfulLine(code: string): string {
  for (const line of code.split('\n')) {
    const t = line.trim();
    if (t) { return t.slice(0, 140); }
  }
  return '';
}

function getNonce(): string {
  let text = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 24; i++) { text += chars.charAt(Math.floor(Math.random() * chars.length)); }
  return text;
}

export class CaptureWebviewProvider implements vscode.WebviewViewProvider {
  static readonly VIEW_ID = 'lineagelens.captures';

  private view?: vscode.WebviewView;
  private lastPost = { records: 0, ok: true, error: '', at: '' };
  private postSeq = 0;

  constructor(
    private readonly store: CaptureStore,
    private readonly onMutate: () => void,
  ) { }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };

    webviewView.webview.onDidReceiveMessage((msg: { type: string; id?: string; source?: string; command?: string; error?: string }) => {
      switch (msg.type) {
        case 'ready': this.postRecords(); break;
        case 'webviewError':
          this.lastPost = { ...this.lastPost, ok: false, error: msg.error ?? 'Unknown webview error' };
          console.error('LineageLens Base sidebar webview failed:', msg.error);
          break;
        case 'open': if (msg.id) { vscode.commands.executeCommand('lineagelens.openCapture', msg.id); } break;
        case 'insert': if (msg.id) { vscode.commands.executeCommand('lineagelens.insertAtCursor', msg.id); } break;
        case 'copy': if (msg.id) { vscode.commands.executeCommand('lineagelens.copyCode', msg.id); } break;
        case 'reveal': if (msg.id) { vscode.commands.executeCommand('lineagelens.revealInFile', msg.id); } break;
        case 'delete': if (msg.id) { vscode.commands.executeCommand('lineagelens.deleteCapture', msg.id); } break;
        case 'reviewed': {
          if (msg.id && this.store.setReviewState(msg.id, 'reviewed')) { this.onMutate(); }
          break;
        }
        case 'clearAll': vscode.commands.executeCommand('lineagelens.clearAll'); break;
        case 'cmd': if (msg.command && SIDEBAR_COMMANDS.has(msg.command)) { vscode.commands.executeCommand(msg.command); } break;
        case 'reclassify': {
          const src = msg.source as CaptureSource;
          if (msg.id && (src === 'ai' || src === 'paste' || src === 'unknown')) {
            if (this.store.setClassification(msg.id, src)) { this.onMutate(); }
          }
          break;
        }
      }
    });

    webviewView.onDidChangeVisibility(() => { if (webviewView.visible) { this.postRecords(); } });
    webviewView.onDidDispose(() => { if (this.view === webviewView) { this.view = undefined; } });

    webviewView.webview.html = this.html(webviewView.webview);
    for (const delay of [0, 75, 250, 750, 1500]) {
      setTimeout(() => { this.postRecords(); }, delay);
    }
  }

  refresh(): void { this.postRecords(); }

  diagnostics(): string {
    return [
      `viewLive=${Boolean(this.view)}`,
      `viewVisible=${this.view?.visible ?? false}`,
      `lastPostedRecords=${this.lastPost.records}`,
      `lastPostOk=${this.lastPost.ok}`,
      `lastPostAt=${this.lastPost.at || 'never'}`,
      `lastPostError=${this.lastPost.error || 'none'}`,
    ].join('\n');
  }

  private postRecords(): void {
    if (!this.view) { return; }
    const cards: CardData[] = this.store.getAll().map(r => ({
      id: r.id,
      fileName: r.fileName,
      language: r.language,
      linesAdded: r.linesAdded,
      timestamp: r.timestamp,
      confidence: r.confidence ?? 0.5,
      source: r.source ?? 'unknown',
      reviewState: r.reviewState ?? 'unreviewed',
      risk: r.riskSignals && r.riskSignals.length
        ? { label: r.riskSignals[0].label, severity: r.riskSignals[0].severity }
        : null,
      filePath: r.filePath,
      workspaceFolder: r.workspaceFolder,
      preview: firstMeaningfulLine(r.insertedCode ?? ''),
    }));

    const cfg = vscode.workspace.getConfiguration('lineagelensBase');
    const backendUrl = (cfg.get<string>('backendUrl', '') ?? '').trim();
    const workspaceId = (cfg.get<string>('workspaceId', '') ?? '').trim();
    const server = backendUrl ? { backendUrl, workspaceId: workspaceId || 'default' } : null;
    const stats = {
      total: cards.length,
      needsReview: cards.filter(c => c.reviewState === 'unreviewed' || c.reviewState === 'needs_changes').length,
      highRisk: cards.filter(c => c.risk?.severity === 'high').length,
      ai: cards.filter(c => c.source === 'ai').length,
      paste: cards.filter(c => c.source === 'paste').length,
    };

    const seq = ++this.postSeq;
    this.lastPost = { records: cards.length, ok: true, error: '', at: new Date().toISOString() };
    this.view.webview.postMessage({ type: 'data', seq, records: cards, server, stats }).then(
      ok => {
        if (!ok) {
          this.lastPost = { records: cards.length, ok: false, error: 'postMessage returned false', at: new Date().toISOString() };
          console.warn('LineageLens Base sidebar postMessage returned false');
        }
      },
      err => {
        this.lastPost = { records: cards.length, ok: false, error: String(err), at: new Date().toISOString() };
        console.error('LineageLens Base sidebar postMessage failed:', err);
      },
    );
  }

  private html(webview: vscode.Webview): string {
    const nonce = getNonce();
    const csp = [
      `default-src 'none'`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
    ].join('; ');

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<title>LineageLens Captures</title>
<style>
  :root {
    --bg: var(--vscode-sideBar-background, #161616);
    --panel: color-mix(in srgb, var(--vscode-sideBar-background, #161616) 82%, white 7%);
    --panel2: color-mix(in srgb, var(--vscode-sideBar-background, #161616) 74%, white 10%);
    --line: color-mix(in srgb, var(--vscode-sideBar-foreground, #d4d4d4) 14%, transparent);
    --muted: var(--vscode-descriptionForeground, #8f8f8f);
    --text: var(--vscode-sideBar-foreground, var(--vscode-foreground));
    --accent: #8b95ff;
    --green: #39d98a;
    --amber: #f5b342;
    --red: #ff6b6b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    font-family: var(--vscode-font-family);
    font-size: 12px;
    color: var(--text);
    background:
      radial-gradient(520px 260px at 12% -6%, rgba(139,149,255,.15), transparent 58%),
      radial-gradient(360px 220px at 90% 8%, rgba(57,217,138,.08), transparent 60%),
      var(--bg);
    padding: 8px 10px 22px;
  }
  button, input, select { font: inherit; }
  button { color: inherit; }

  .top {
    position: sticky; top: 0; z-index: 10;
    margin: -8px -10px 10px; padding: 8px 10px 9px;
    background: linear-gradient(180deg, var(--bg) 82%, transparent);
    backdrop-filter: blur(10px);
  }
  .total-pill {
    flex: none; display: inline-flex; align-items: baseline; gap: 4px;
    border: 1px solid var(--line); background: rgba(255,255,255,.055);
    border-radius: 999px; padding: 4px 8px;
  }
  .total-pill strong { font-size: 12px; }
  .total-pill span { color: var(--muted); font-size: 10px; }

  .status-row { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
  .server {
    flex: 1; min-width: 0; display: flex; align-items: center; gap: 7px;
    color: var(--muted); border: 1px solid var(--line); background: rgba(255,255,255,.035);
    border-radius: 9px; padding: 5px 8px;
  }
  .server-dot { width: 7px; height: 7px; border-radius: 50%; background: #747474; flex: none; }
  .server-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .server-count { margin-left: auto; color: var(--text); font-size: 10.5px; white-space: nowrap; }
  .ghost-btn {
    flex: none; border: 1px solid var(--line); background: rgba(255,255,255,.045);
    border-radius: 9px; padding: 5px 8px; color: var(--text); cursor: pointer;
  }
  .ghost-btn:hover { border-color: color-mix(in srgb, var(--accent) 60%, var(--line)); background: rgba(139,149,255,.12); }

  .searchbox {
    display: flex; align-items: center; gap: 8px;
    border: 1px solid var(--line); background: rgba(255,255,255,.06);
    border-radius: 10px; padding: 6px 9px; margin-bottom: 7px;
  }
  .searchbox:focus-within { border-color: color-mix(in srgb, var(--accent) 75%, white 5%); box-shadow: 0 0 0 1px rgba(139,149,255,.14); }
  .mag { color: var(--muted); font-size: 13px; }
  #search { flex: 1; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--text); }
  #search::placeholder { color: color-mix(in srgb, var(--muted) 78%, transparent); }

  .chips { display: flex; gap: 5px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; }
  .chips::-webkit-scrollbar { display: none; }
  .chip {
    flex: none; border: 1px solid var(--line); background: rgba(255,255,255,.035);
    border-radius: 999px; padding: 4px 8px; color: var(--muted); cursor: pointer; white-space: nowrap;
  }
  .chip strong { color: var(--text); font-weight: 650; }
  .chip:hover { color: var(--text); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
  .chip.active { color: #fff; background: rgba(139,149,255,.18); border-color: rgba(139,149,255,.55); }
  .chip.danger.active { background: rgba(255,107,107,.16); border-color: rgba(255,107,107,.55); }

  .controls { display: flex; align-items: center; gap: 7px; margin: 7px 0 9px; }
  .controls select {
    min-width: 0; flex: 1; border: 1px solid var(--line); border-radius: 10px;
    background: rgba(255,255,255,.045); color: var(--text); padding: 5px 8px; outline: 0;
  }
  .action {
    border: 1px solid var(--line); border-radius: 10px; padding: 5px 8px;
    background: rgba(255,255,255,.045); color: var(--text); cursor: pointer; white-space: nowrap;
  }
  .action.primary { border-color: rgba(57,217,138,.35); background: rgba(57,217,138,.11); }
  .action:hover { border-color: rgba(139,149,255,.55); }
  .more { position: relative; }
  .more-menu {
    display: none; position: absolute; right: 0; top: 31px; min-width: 150px; z-index: 20;
    border: 1px solid var(--line); border-radius: 12px; padding: 5px;
    background: color-mix(in srgb, var(--bg) 88%, white 8%); box-shadow: 0 10px 32px rgba(0,0,0,.32);
  }
  .more.open .more-menu { display: block; }
  .more-menu button { display: block; width: 100%; text-align: left; border: 0; background: transparent; border-radius: 8px; padding: 7px 8px; cursor: pointer; }
  .more-menu button:hover { background: rgba(255,255,255,.07); }
  .clear { color: var(--red); }

  #list { margin: 0 -10px; }
  .group-head {
    display: flex; align-items: center; gap: 7px; margin: 15px 0 5px; padding: 0 12px;
    color: var(--muted); font-size: 10px; font-weight: 760; letter-spacing: .08em; text-transform: uppercase;
    cursor: pointer; user-select: none;
  }
  .group-head .gcount { margin-left: auto; color: var(--text); background: rgba(255,255,255,.07); border-radius: 999px; padding: 1px 7px; letter-spacing: 0; text-transform: none; }
  .group-head.collapsed .caret { transform: rotate(-90deg); }

  .card {
    position: relative; margin: 0 0 7px; padding: 10px 11px 9px 13px;
    border-top: 1px solid transparent; border-bottom: 1px solid var(--line);
    background: linear-gradient(90deg, rgba(139,149,255,.07), transparent 46%);
    cursor: pointer;
  }
  .card:hover, .card:focus-within { background: rgba(255,255,255,.045); border-top-color: var(--line); }
  .card::before { content: ''; position: absolute; left: 0; top: 10px; bottom: 10px; width: 3px; border-radius: 0 4px 4px 0; background: #747474; }
  .card.s-ai::before { background: var(--accent); }
  .card.s-paste::before { background: var(--amber); }
  .card-main { display: flex; align-items: flex-start; gap: 9px; }
  .card-title { min-width: 0; flex: 1; }
  .file { font-size: 12.8px; font-weight: 740; letter-spacing: -.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .path { margin-top: 3px; color: var(--muted); font-size: 10.4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pill-stack { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 5px; max-width: 42%; }
  .badge { border: 1px solid var(--line); border-radius: 999px; padding: 2px 7px; font-size: 9.7px; color: var(--muted); white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
  .risk-high { color: var(--red); border-color: rgba(255,107,107,.5); background: rgba(255,107,107,.11); }
  .risk-medium { color: var(--amber); border-color: rgba(245,179,66,.48); background: rgba(245,179,66,.1); }
  .review-new { color: var(--muted); }
  .review-ok { color: var(--green); border-color: rgba(57,217,138,.45); background: rgba(57,217,138,.08); }
  .review-bad { color: var(--red); border-color: rgba(255,107,107,.45); background: rgba(255,107,107,.09); }
  .meta { display: flex; align-items: center; gap: 6px; margin-top: 7px; color: var(--muted); font-size: 10.5px; }
  .meta b { color: var(--green); font-weight: 720; }
  .source-dot { width: 6px; height: 6px; border-radius: 50%; background: #747474; }
  .source-dot.ai { background: var(--accent); }
  .source-dot.paste { background: var(--amber); }
  .preview { margin-top: 8px; color: color-mix(in srgb, var(--muted) 84%, white 8%); font-family: var(--vscode-editor-font-family, monospace); font-size: 10.7px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-actions { display: none; gap: 5px; margin-top: 9px; }
  .card:hover .card-actions, .card:focus-within .card-actions { display: flex; }
  .card-actions button { flex: 1; border: 1px solid var(--line); border-radius: 8px; background: rgba(255,255,255,.045); padding: 5px 6px; cursor: pointer; font-size: 10.5px; }
  .card-actions button:hover { border-color: rgba(139,149,255,.55); background: rgba(139,149,255,.12); }
  .card-actions .ok { flex: .7; color: var(--green); border-color: rgba(57,217,138,.35); }
  .card-actions .del { flex: .7; color: var(--red); }

  .state { text-align: center; padding: 54px 22px; color: var(--muted); }
  .state .mark { width: 42px; height: 42px; margin: 0 auto 13px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 14px; background: rgba(255,255,255,.04); color: var(--text); font-size: 18px; }
  .state .title { color: var(--text); font-weight: 720; margin-bottom: 5px; }
  .state .copy { font-size: 11px; line-height: 1.55; }
  .hidden { display: none !important; }
</style>
</head>
<body>
  <div class="top">
    <div class="status-row">
      <div class="server" title="Capture mode">
        <span id="server-dot" class="server-dot"></span>
        <span id="server-text" class="server-text">Local mode</span>
        <span id="subtitle" class="server-count">0 captures</span>
      </div>
      <button id="server-act-btn" class="ghost-btn" data-cmd="lineagelensBase.connectToServer" type="button">Connect</button>
    </div>

    <label class="searchbox">
      <span class="mag">Search</span>
      <input id="search" type="text" placeholder="files, risks, source, review state" />
    </label>

    <div class="chips" role="toolbar" aria-label="Capture filters">
      <button class="chip active" data-filter="all" type="button">All <strong id="chip-all">0</strong></button>
      <button class="chip" data-filter="inbox" type="button">Inbox <strong id="chip-inbox">0</strong></button>
      <button class="chip danger" data-filter="risk" type="button">High risk <strong id="chip-risk">0</strong></button>
      <button class="chip" data-filter="ai" type="button">AI <strong id="chip-ai">0</strong></button>
      <button class="chip" data-filter="paste" type="button">Paste <strong id="chip-paste">0</strong></button>
    </div>

    <div class="controls">
      <select id="group" title="Group captures">
        <option value="none">No grouping</option>
        <option value="file">By file</option>
        <option value="source">By source</option>
        <option value="day">By day</option>
      </select>
      <button class="action primary" data-cmd="lineagelens.verifyEvidence" type="button">Verify</button>
      <div class="more" id="more">
        <button class="action" id="moreBtn" type="button">More</button>
        <div class="more-menu" id="moreMenu">
          <button data-cmd="lineagelens.searchCaptures" type="button">Find capture</button>
          <button data-cmd="lineagelens.exportCapsule" type="button">Export capsule</button>
          <button data-cmd="lineagelens.exportPrSummary" type="button">PR summary</button>
          <button data-cmd="lineagelens.checkBeforeCommit" type="button">Pre-commit check</button>
          <button data-cmd="lineagelens.showFileTimeline" type="button">File timeline</button>
          <button data-cmd="lineagelens.toggleFocusMode" type="button">Focus mode</button>
          <button data-cmd="lineagelens.exportJson" type="button">Export JSON</button>
          <button data-cmd="lineagelens.exportAgentTrace" type="button">Agent trace</button>
          <button id="clearAll" class="clear" type="button">Clear all</button>
        </div>
      </div>
    </div>
  </div>

  <div id="list"></div>
  <div id="error" class="state hidden">
    <div class="mark">!</div>
    <div class="title">Sidebar render failed</div>
    <div class="copy" id="errorText">Run LineageLens: Diagnostics.</div>
  </div>
  <div id="empty" class="state hidden">
    <div class="mark">LL</div>
    <div class="title">No captures yet</div>
    <div class="copy">Accept or paste 4+ lines of code. LineageLens will add the evidence here automatically.</div>
  </div>

  <script nonce="${nonce}">
    (function() {
      var vscode = acquireVsCodeApi();
      var RECORDS = [];
      var STATS = { total: 0, needsReview: 0, highRisk: 0, ai: 0, paste: 0 };
      var query = '';
      var group = 'none';
      var filter = 'all';
      var collapsed = new Set();

      function el(id) { return document.getElementById(id); }
      function text(id, value) { var node = el(id); if (node) node.textContent = String(value); }
      function esc(value) {
        return String(value == null ? '' : value)
          .split('&').join('&amp;')
          .split('<').join('&lt;')
          .split('>').join('&gt;')
          .split('"').join('&quot;');
      }
      function lower(value) { return String(value == null ? '' : value).toLowerCase(); }
      function showError(err) {
        var msg = err && (err.stack || err.message || String(err)) || 'Unknown sidebar error';
        var list = el('list'), empty = el('empty'), box = el('error'), errorText = el('errorText');
        if (list) list.innerHTML = '';
        if (empty) empty.classList.add('hidden');
        if (errorText) errorText.textContent = msg;
        if (box) box.classList.remove('hidden');
        try { vscode.postMessage({ type: 'webviewError', error: msg }); } catch (_) {}
      }
      window.addEventListener('error', function(e) { showError(e.error || e.message); });
      window.addEventListener('unhandledrejection', function(e) { showError(e.reason); });

      function parentPath(filePath) {
        var value = String(filePath || '');
        var i = Math.max(value.lastIndexOf('/'), value.lastIndexOf(String.fromCharCode(92)));
        return i >= 0 ? value.slice(0, i) : '';
      }
      function displayUrl(url) {
        var value = String(url || '');
        if (value.indexOf('https://') === 0) return value.slice(8);
        if (value.indexOf('http://') === 0) return value.slice(7);
        return value;
      }
      function relTime(iso) {
        var time = new Date(iso).getTime();
        if (!time) return 'unknown';
        var secs = Math.floor((Date.now() - time) / 1000);
        if (secs < 60) return 'now';
        if (secs < 3600) return Math.floor(secs / 60) + 'm';
        if (secs < 86400) return Math.floor(secs / 3600) + 'h';
        if (secs < 172800) return 'yesterday';
        return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
      }
      function needsReview(r) {
        return (r.reviewState || 'unreviewed') === 'unreviewed' || r.reviewState === 'needs_changes';
      }
      function sourceLabel(source) {
        if (source === 'ai') return 'AI';
        if (source === 'paste') return 'Paste';
        return 'Unknown';
      }
      function reviewBadge(state) {
        if (state === 'reviewed' || state === 'accepted') return '<span class="badge review-ok">reviewed</span>';
        if (state === 'needs_changes') return '<span class="badge review-bad">needs changes</span>';
        if (state === 'rejected') return '<span class="badge review-bad">rejected</span>';
        return '<span class="badge review-new">new</span>';
      }
      function riskBadge(risk) {
        if (!risk) return '';
        var cls = risk.severity === 'high' ? 'risk-high' : risk.severity === 'medium' ? 'risk-medium' : '';
        return '<span class="badge ' + cls + '">' + esc(risk.label || risk.severity) + '</span>';
      }
      function dayBucket(iso) {
        var d = new Date(iso);
        if (!d.getTime()) return { key: 'unknown', label: 'Unknown date' };
        var now = new Date();
        var start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
        var then = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
        var delta = Math.round((start - then) / 86400000);
        var key = d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate();
        if (delta === 0) return { key: key, label: 'Today' };
        if (delta === 1) return { key: key, label: 'Yesterday' };
        return { key: key, label: d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) };
      }

      function matchesQuery(r, q) {
        if (!q) return true;
        var colon = q.indexOf(':');
        if (colon > 0) {
          var field = q.slice(0, colon);
          var value = q.slice(colon + 1).trim();
          if (value) {
            if (field === 'risk') return lower((r.risk && r.risk.severity) || '').indexOf(value) >= 0 || lower((r.risk && r.risk.label) || '').indexOf(value) >= 0;
            if (field === 'review') return lower(r.reviewState || 'unreviewed').indexOf(value) >= 0;
            if (field === 'source') return lower(r.source || '').indexOf(value) >= 0;
          }
        }
        return lower(r.fileName).indexOf(q) >= 0
          || lower(r.language).indexOf(q) >= 0
          || lower(r.source).indexOf(q) >= 0
          || lower(r.reviewState).indexOf(q) >= 0
          || lower(r.filePath).indexOf(q) >= 0
          || lower(r.preview).indexOf(q) >= 0
          || lower((r.risk && r.risk.label) || '').indexOf(q) >= 0;
      }
      function matchesFilter(r) {
        if (filter === 'inbox') return needsReview(r);
        if (filter === 'risk') return r.risk && r.risk.severity === 'high';
        if (filter === 'ai') return r.source === 'ai';
        if (filter === 'paste') return r.source === 'paste';
        return true;
      }
      function visibleRecords() {
        var q = query.trim().toLowerCase();
        return RECORDS.filter(function(r) { return matchesFilter(r) && matchesQuery(r, q); });
      }
      function groupKeyFor(r) {
        if (group === 'file') return 'file:' + (r.filePath || r.fileName || 'unknown');
        if (group === 'source') return 'source:' + (r.source || 'unknown');
        return 'day:' + dayBucket(r.timestamp).key;
      }
      function groupsFor(records) {
        var map = new Map();
        records.forEach(function(r) {
          var key = groupKeyFor(r);
          var label;
          if (group === 'file') label = (r.fileName || 'unknown') + (r.filePath && r.filePath !== r.fileName ? ' - ' + parentPath(r.filePath) : '');
          else if (group === 'source') label = sourceLabel(r.source);
          else label = dayBucket(r.timestamp).label;
          var item = map.get(key);
          if (item) item.records.push(r); else map.set(key, { label: label, records: [r] });
        });
        Array.from(collapsed).forEach(function(k) { if (!map.has(k)) collapsed.delete(k); });
        return Array.from(map.entries());
      }
      function cardHtml(r) {
        var pct = Math.round(Number(r.confidence == null ? 0.5 : r.confidence) * 100);
        var parent = parentPath(r.filePath);
        var pathLabel = [r.language || 'text', parent || r.workspaceFolder || '', relTime(r.timestamp)].filter(Boolean).join(' / ');
        var sourceClass = r.source === 'ai' ? 'ai' : r.source === 'paste' ? 'paste' : '';
        var source = '<span class="source-dot ' + sourceClass + '"></span><span>' + sourceLabel(r.source) + '</span>';
        var preview = r.preview ? '<div class="preview">' + esc(r.preview) + '</div>' : '';
        var done = needsReview(r) ? '<button class="ok" data-act="reviewed" type="button">Done</button>' : '';
        return '<article class="card s-' + esc(r.source || 'unknown') + '" data-id="' + esc(r.id) + '">'
          + '<div class="card-main">'
          + '<div class="card-title"><div class="file" title="' + esc(r.filePath) + '">' + esc(r.fileName || 'unknown') + '</div><div class="path">' + esc(pathLabel) + '</div></div>'
          + '<div class="pill-stack">' + riskBadge(r.risk) + reviewBadge(r.reviewState) + '</div>'
          + '</div>'
          + '<div class="meta">' + source + '<span>+<b>' + esc(Number(r.linesAdded) || 0) + '</b> lines</span><span>' + esc(pct) + '% confidence</span></div>'
          + preview
          + '<div class="card-actions">' + done + '<button data-act="open" type="button">Open</button><button data-act="copy" type="button">Copy</button><button data-act="reveal" type="button">File</button><button class="del" data-act="delete" type="button">Delete</button></div>'
          + '</article>';
      }
      function updateTop(visible) {
        text('chip-all', STATS.total || RECORDS.length);
        text('chip-inbox', STATS.needsReview || 0);
        text('chip-risk', STATS.highRisk || 0);
        text('chip-ai', STATS.ai || 0);
        text('chip-paste', STATS.paste || 0);
        var sub = visible.length === RECORDS.length ? RECORDS.length + ' captures' : visible.length + '/' + RECORDS.length;
        text('subtitle', sub);
        Array.prototype.forEach.call(document.querySelectorAll('.chip'), function(btn) {
          btn.classList.toggle('active', btn.getAttribute('data-filter') === filter);
        });
      }
      function render() {
        var list = el('list'), empty = el('empty'), error = el('error');
        if (error) error.classList.add('hidden');
        var visible = visibleRecords();
        updateTop(visible);
        if (!list || !empty) return;
        if (RECORDS.length === 0) {
          list.innerHTML = '';
          empty.classList.remove('hidden');
          return;
        }
        empty.classList.add('hidden');
        if (group === 'none') {
          list.innerHTML = visible.map(cardHtml).join('') || '<div class="state"><div class="mark">0</div><div class="title">No matches</div><div class="copy">Try All captures or clear the search.</div></div>';
          return;
        }
        var html = '';
        groupsFor(visible).forEach(function(entry) {
          var key = entry[0], value = entry[1], isCollapsed = collapsed.has(key);
          html += '<div class="group-head ' + (isCollapsed ? 'collapsed' : '') + '" data-group="' + esc(key) + '"><span class="caret">v</span><span>' + esc(value.label) + '</span><span class="gcount">' + value.records.length + '</span></div>';
          if (!isCollapsed) html += value.records.map(cardHtml).join('');
        });
        list.innerHTML = html || '<div class="state"><div class="mark">0</div><div class="title">No matches</div><div class="copy">Try All captures or clear the search.</div></div>';
      }
      function setServer(server) {
        var dot = el('server-dot'), label = el('server-text'), btn = el('server-act-btn');
        if (!dot || !label || !btn) return;
        if (server) {
          dot.style.background = '#39d98a';
          dot.style.boxShadow = '0 0 8px rgba(57,217,138,.55)';
          label.textContent = server.workspaceId + ' / ' + displayUrl(server.backendUrl);
          btn.textContent = 'Disconnect';
          btn.setAttribute('data-cmd', 'lineagelensBase.disconnectServer');
        } else {
          dot.style.background = '#747474';
          dot.style.boxShadow = 'none';
          label.textContent = 'Local mode';
          btn.textContent = 'Connect';
          btn.setAttribute('data-cmd', 'lineagelensBase.connectToServer');
        }
      }
      function postCommand(command) { if (command) vscode.postMessage({ type: 'cmd', command: command }); }

      el('search').addEventListener('input', function(e) { try { query = e.target.value || ''; render(); } catch (err) { showError(err); } });
      el('group').addEventListener('change', function(e) { try { group = e.target.value || 'none'; collapsed.clear(); render(); } catch (err) { showError(err); } });
      el('moreBtn').addEventListener('click', function(e) { e.stopPropagation(); el('more').classList.toggle('open'); });
      document.addEventListener('click', function() { var more = el('more'); if (more) more.classList.remove('open'); });
      Array.prototype.forEach.call(document.querySelectorAll('.chip'), function(btn) {
        btn.addEventListener('click', function() { try { filter = btn.getAttribute('data-filter') || 'all'; render(); } catch (err) { showError(err); } });
      });
      Array.prototype.forEach.call(document.querySelectorAll('[data-cmd]'), function(btn) {
        btn.addEventListener('click', function(e) { e.stopPropagation(); postCommand(btn.getAttribute('data-cmd')); });
      });
      el('clearAll').addEventListener('click', function(e) { e.stopPropagation(); vscode.postMessage({ type: 'clearAll' }); });
      el('list').addEventListener('click', function(e) {
        try {
          var target = e.target && e.target.closest ? e.target : null;
          if (!target) return;
          var groupHead = target.closest('.group-head');
          if (groupHead) {
            var k = groupHead.getAttribute('data-group');
            if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k);
            render();
            return;
          }
          var card = target.closest('.card');
          if (!card) return;
          var id = card.getAttribute('data-id');
          var action = target.closest('[data-act]');
          if (action) {
            e.stopPropagation();
            var act = action.getAttribute('data-act');
            vscode.postMessage({ type: act === 'open' ? 'open' : act, id: id });
            return;
          }
          vscode.postMessage({ type: 'open', id: id });
        } catch (err) { showError(err); }
      });
      window.addEventListener('message', function(ev) {
        try {
          var msg = ev.data;
          if (!msg || msg.type !== 'data') return;
          RECORDS = Array.isArray(msg.records) ? msg.records : [];
          STATS = msg.stats || STATS;
          setServer(msg.server);
          render();
        } catch (err) { showError(err); }
      });

      vscode.postMessage({ type: 'ready' });
    })();
  </script>
</body>
</html>`;
  }
}
