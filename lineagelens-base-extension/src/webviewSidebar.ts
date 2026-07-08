import * as vscode from 'vscode';
import { CaptureStore, CaptureSource, ReviewState } from './store';

/** Lightweight per-card payload sent to the webview (no full code body). */
interface CardData {
  id: string;
  fileName: string;
  language: string;
  linesAdded: number;
  timestamp: string;
  confidence: number;
  source: CaptureSource;
  reviewState: ReviewState;
  /** Highest-severity risk signal, if any (for the row badge). */
  risk: { label: string; severity: string } | null;
  filePath: string;
  workspaceFolder: string | null;
  preview: string;
}

// Commands the sidebar toolbar may invoke. Allowlisted so the webview can never
// execute an arbitrary command id.
const SIDEBAR_COMMANDS = new Set<string>([
  'lineagelens.openWelcome',
  'lineagelens.verifyEvidence',
  'lineagelens.exportCapsule',
  'lineagelens.exportPrSummary',
  'lineagelens.checkBeforeCommit',
  'lineagelens.showFileTimeline',
  'lineagelens.toggleFocusMode',
  'lineagelens.exportJson',
  'lineagelens.exportAgentTrace',
]);

function firstMeaningfulLine(code: string): string {
  for (const line of code.split('\n')) {
    const t = line.trim();
    if (t) { return t.slice(0, 120); }
  }
  return '';
}

function getNonce(): string {
  let text = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 24; i++) { text += chars.charAt(Math.floor(Math.random() * chars.length)); }
  return text;
}

/**
 * Card-stack sidebar rendered as a webview (replaces the native tree so the
 * panel can carry its own background, elevation, and inline search/grouping).
 * All record mutations are delegated to existing commands / the store; the
 * webview is a pure view that re-renders whenever {@link refresh} is called.
 */
export class CaptureWebviewProvider implements vscode.WebviewViewProvider {
  static readonly VIEW_ID = 'lineagelens.captures';

  private view?: vscode.WebviewView;

  constructor(
    private readonly store: CaptureStore,
    private readonly onMutate: () => void,
  ) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = this.html(webviewView.webview);

    webviewView.webview.onDidReceiveMessage((msg: { type: string; id?: string; source?: string; command?: string }) => {
      switch (msg.type) {
        case 'ready':   this.postRecords(); break;
        case 'open':    if (msg.id) { vscode.commands.executeCommand('lineagelens.openCapture', msg.id); } break;
        case 'insert':  if (msg.id) { vscode.commands.executeCommand('lineagelens.insertAtCursor', msg.id); } break;
        case 'copy':    if (msg.id) { vscode.commands.executeCommand('lineagelens.copyCode', msg.id); } break;
        case 'reveal':  if (msg.id) { vscode.commands.executeCommand('lineagelens.revealInFile', msg.id); } break;
        case 'delete':  if (msg.id) { vscode.commands.executeCommand('lineagelens.deleteCapture', msg.id); } break;
        case 'clearAll': vscode.commands.executeCommand('lineagelens.clearAll'); break;
        case 'cmd':     if (msg.command && SIDEBAR_COMMANDS.has(msg.command)) { vscode.commands.executeCommand(msg.command); } break;
        case 'reclassify': {
          const src = msg.source as CaptureSource;
          if (msg.id && (src === 'ai' || src === 'paste' || src === 'unknown')) {
            if (this.store.setClassification(msg.id, src)) { this.onMutate(); }
          }
          break;
        }
      }
    });

    // Re-send data whenever the view becomes visible again (state can change while hidden).
    webviewView.onDidChangeVisibility(() => { if (webviewView.visible) { this.postRecords(); } });
  }

  /** Push the current store contents to the webview, if it is live. */
  refresh(): void { this.postRecords(); }

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
    this.view.webview.postMessage({ type: 'data', records: cards });
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
<title>AI Captures</title>
<style>
  :root {
    --accent-ai: #7c8cff;
    --accent-paste: #f5a623;
    --accent-unknown: #8a8a8a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    font-family: var(--vscode-font-family);
    font-size: 12px;
    color: var(--vscode-foreground);
    /* The custom backdrop: a soft indigo glow over the theme's sidebar colour. */
    background:
      radial-gradient(130% 70% at 50% -8%, rgba(124,140,255,.12), transparent 62%),
      var(--vscode-sideBar-background, var(--vscode-editor-background));
    padding: 10px 10px 24px;
  }

  /* ── Toolbar ────────────────────────────────────────────────────────────── */
  .toolbar {
    position: sticky; top: 0; z-index: 5;
    display: flex; flex-direction: column; gap: 8px;
    padding: 8px 2px 10px;
    background: linear-gradient(var(--vscode-sideBar-background, var(--vscode-editor-background)) 70%, transparent);
    margin: -10px -10px 6px; padding-left: 12px; padding-right: 12px;
  }
  .search {
    display: flex; align-items: center; gap: 6px;
    background: var(--vscode-input-background);
    border: 1px solid var(--vscode-input-border, transparent);
    border-radius: 8px; padding: 5px 9px;
  }
  .search:focus-within { border-color: var(--vscode-focusBorder); }
  .search svg { opacity: .6; flex: none; }
  .search input {
    flex: 1; background: transparent; border: none; outline: none;
    color: var(--vscode-input-foreground); font-family: inherit; font-size: 12px;
  }
  .tools { display: flex; align-items: center; gap: 8px; }
  .tools select {
    flex: 1; background: var(--vscode-dropdown-background);
    color: var(--vscode-dropdown-foreground);
    border: 1px solid var(--vscode-dropdown-border, transparent);
    border-radius: 7px; padding: 4px 8px; font-family: inherit; font-size: 11px; outline: none;
  }
  .count { font-size: 11px; color: var(--vscode-descriptionForeground); white-space: nowrap; }
  .clear-btn {
    background: transparent; border: 1px solid var(--vscode-panel-border);
    color: var(--vscode-descriptionForeground);
    border-radius: 7px; padding: 4px 9px; font-size: 11px; cursor: pointer; font-family: inherit;
  }
  .clear-btn:hover { color: var(--vscode-errorForeground); border-color: var(--vscode-errorForeground); }

  /* ── Actions bar ────────────────────────────────────────────────────────── */
  .actions-bar { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 2px; }
  .tool-btn {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--vscode-widget-border, transparent);
    border-radius: 6px; padding: 3px 8px; font-size: 10.5px; cursor: pointer;
    font-family: inherit; white-space: nowrap;
  }
  .tool-btn:hover { background: var(--vscode-button-secondaryHoverBackground); }

  /* ── Group header ───────────────────────────────────────────────────────── */
  .group-head {
    display: flex; align-items: center; gap: 6px;
    margin: 14px 0 4px; padding: 0 14px; font-size: 10px; font-weight: 700;
    letter-spacing: .07em; text-transform: uppercase;
    color: var(--vscode-descriptionForeground); cursor: pointer; user-select: none;
  }
  .group-head .caret { transition: transform .15s ease; }
  .group-head.collapsed .caret { transform: rotate(-90deg); }
  .group-head .gcount {
    margin-left: auto; font-weight: 600; letter-spacing: 0; text-transform: none;
    background: var(--vscode-badge-background); color: var(--vscode-badge-foreground);
    border-radius: 9px; padding: 1px 7px;
  }

  /* ── Row (compact list) ─────────────────────────────────────────────────── */
  #list { margin: 0 -10px; }
  .row {
    position: relative;
    padding: 7px 12px 7px 14px;
    border-bottom: 1px solid var(--vscode-widget-border, rgba(255,255,255,.05));
    cursor: pointer;
    transition: background .1s ease;
  }
  .row::before {
    content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--accent-unknown);
  }
  .row.s-ai::before    { background: var(--accent-ai); }
  .row.s-paste::before { background: var(--accent-paste); }
  .row:hover { background: var(--vscode-list-hoverBackground); }

  .row-top { display: flex; align-items: center; gap: 8px; }
  .fname {
    font-weight: 600; font-size: 12.5px; flex: 1;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .badges { display: flex; align-items: center; gap: 5px; flex: none; }
  .b {
    font-size: 9.5px; font-weight: 700; padding: 1px 6px; border-radius: 9px;
    white-space: nowrap; border: 1px solid transparent; letter-spacing: .01em;
  }
  .b-rev-ok   { color: #34d058; border-color: rgba(52,208,88,.4); }
  .b-rev-warn { color: #f44747; border-color: rgba(244,71,71,.4); }
  .b-rev-new  { color: var(--vscode-descriptionForeground); border-color: var(--vscode-panel-border); opacity: .8; }
  .b-risk-high { color: #f44747; background: rgba(244,71,71,.12); border-color: rgba(244,71,71,.4); }
  .b-risk-med  { color: #f5a623; background: rgba(245,166,35,.12); border-color: rgba(245,166,35,.4); }
  .b-risk-low  { color: var(--vscode-descriptionForeground); border-color: var(--vscode-panel-border); }

  .row-sub { display: flex; align-items: center; margin-top: 3px; font-size: 10.5px; color: var(--vscode-descriptionForeground); }
  .row-sub .dot { opacity: .45; margin: 0 5px; }
  .row-sub .lines { color: #34d058; font-weight: 600; }
  .conf { margin-left: auto; display: inline-flex; align-items: center; gap: 4px; opacity: .9; }
  .conf .cdot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-unknown); }
  .conf.hi .cdot  { background: #34d058; }
  .conf.mid .cdot { background: #f5a623; }
  .conf.lo .cdot  { background: #f44747; }

  /* Preview + actions reveal on hover only — keeps the list quiet by default. */
  .reveal { max-height: 0; opacity: 0; overflow: hidden; transition: max-height .16s ease, opacity .16s ease; }
  .row:hover .reveal { max-height: 90px; opacity: 1; margin-top: 7px; }
  .preview {
    font-family: var(--vscode-editor-font-family, monospace); font-size: 10.5px;
    color: var(--vscode-descriptionForeground);
    background: var(--vscode-textCodeBlock-background, rgba(0,0,0,.18));
    border-radius: 5px; padding: 3px 8px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .row-actions { display: flex; gap: 4px; margin-top: 6px; }
  .act {
    flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 4px;
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: none; border-radius: 5px; padding: 4px 6px; font-size: 10.5px; cursor: pointer; font-family: inherit;
  }
  .act:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .act.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .act.primary:hover { background: var(--vscode-button-hoverBackground); }
  .act.del { flex: 0 0 30px; }
  .act.del:hover { background: var(--vscode-errorForeground); color: var(--vscode-editor-background); }

  /* ── Empty state ────────────────────────────────────────────────────────── */
  .empty { text-align: center; padding: 48px 18px; color: var(--vscode-descriptionForeground); }
  .empty .big { font-size: 30px; margin-bottom: 12px; opacity: .8; }
  .empty .t { font-weight: 600; margin-bottom: 6px; color: var(--vscode-foreground); }
  .empty .s { font-size: 11px; line-height: 1.6; }
  .hidden { display: none; }
</style>
</head>
<body>
  <div class="toolbar">
    <label class="search">
      <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4">
        <circle cx="7" cy="7" r="4.5"/><path d="M11 11l3.5 3.5"/>
      </svg>
      <input id="search" type="text" placeholder="Search file, language, source, code…" />
    </label>
    <div class="tools">
      <select id="group" title="Group captures">
        <option value="none">No grouping</option>
        <option value="file">Group by file</option>
        <option value="source">Group by source</option>
        <option value="day">Group by day</option>
      </select>
      <span class="count" id="count">0</span>
      <button class="clear-btn" id="clearAll" type="button">Clear all</button>
    </div>
    <div class="actions-bar">
      <button class="tool-btn" data-cmd="lineagelens.verifyEvidence" type="button" title="Verify Local Evidence Store">✓ Verify</button>
      <button class="tool-btn" data-cmd="lineagelens.exportCapsule" type="button" title="Export Evidence Capsule">📦 Capsule</button>
      <button class="tool-btn" data-cmd="lineagelens.exportPrSummary" type="button" title="Generate PR Summary of AI Changes">📝 PR summary</button>
      <button class="tool-btn" data-cmd="lineagelens.checkBeforeCommit" type="button" title="Check AI Changes Before Commit">⎇ Pre-commit</button>
      <button class="tool-btn" data-cmd="lineagelens.showFileTimeline" type="button" title="Show AI Timeline for This File">🕓 Timeline</button>
      <button class="tool-btn" data-cmd="lineagelens.toggleFocusMode" type="button" title="Toggle AI Focus Mode">👁 Focus</button>
      <button class="tool-btn" data-cmd="lineagelens.exportJson" type="button" title="Export Captures as JSON">⬇ JSON</button>
      <button class="tool-btn" data-cmd="lineagelens.exportAgentTrace" type="button" title="Export Agent Trace">⬇ Trace</button>
    </div>
  </div>

  <div id="list"></div>
  <div id="empty" class="empty hidden">
    <div class="big">⚡</div>
    <div class="t">No captures yet</div>
    <div class="s">LineageLens records AI insertions of 4+ lines automatically.<br>Use Copilot, Cursor, or Claude and they'll appear here.</div>
    <button class="tool-btn" data-cmd="lineagelens.openWelcome" type="button" style="margin-top:14px">❔ Open the guide</button>
  </div>

  <script nonce="${nonce}">
    (function() {
      const vscode = acquireVsCodeApi();
      let RECORDS = [];
      let filter = '';
      let group = 'none';
      const collapsed = new Set();

      const $ = (id) => document.getElementById(id);

      function esc(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      }
      function rel(iso) {
        const d = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
        if (isNaN(d) || d < -60) return 'unknown';
        if (d < 60) return 'just now';
        if (d < 3600) return Math.floor(d/60) + 'm ago';
        if (d < 86400) return Math.floor(d/3600) + 'h ago';
        if (d < 172800) return 'yesterday';
        return new Date(iso).toLocaleDateString([], { month:'short', day:'numeric' });
      }
      function srcLabel(s) { return s === 'ai' ? '🤖 AI' : s === 'paste' ? '📋 Paste' : '❓ Unknown'; }
      function confCls(p) { return p >= 70 ? 'hi' : p >= 45 ? 'mid' : 'lo'; }
      function reviewBadge(s) {
        if (s === 'reviewed' || s === 'accepted') return '<span class="b b-rev-ok">✓ reviewed</span>';
        if (s === 'needs_changes') return '<span class="b b-rev-warn">✎ changes</span>';
        if (s === 'rejected') return '<span class="b b-rev-warn">✕ rejected</span>';
        return '<span class="b b-rev-new">new</span>';
      }
      function riskBadge(r) {
        if (!r) return '';
        var cls = r.severity === 'high' ? 'b-risk-high' : r.severity === 'medium' ? 'b-risk-med' : 'b-risk-low';
        return '<span class="b ' + cls + '" title="local risk signal">⚠ ' + esc(r.label) + '</span>';
      }

      function matches(r, q) {
        return (r.fileName||'').toLowerCase().includes(q)
          || (r.language||'').toLowerCase().includes(q)
          || (r.source||'').toLowerCase().includes(q)
          || (r.filePath||'').toLowerCase().includes(q)
          || (r.preview||'').toLowerCase().includes(q);
      }

      function dayBucket(iso) {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return { key:'unknown', label:'Unknown date' };
        const sod = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
        const delta = Math.round((sod(new Date()) - sod(d)) / 86400000);
        const key = d.getFullYear()+'-'+d.getMonth()+'-'+d.getDate();
        if (delta === 0) return { key, label:'Today' };
        if (delta === 1) return { key, label:'Yesterday' };
        return { key, label: d.toLocaleDateString([], { weekday:'short', month:'short', day:'numeric' }) };
      }

      function cardHtml(r) {
        const pct = Math.round((r.confidence ?? 0.5) * 100);
        const preview = r.preview ? '<div class="preview">' + esc(r.preview) + '</div>' : '';
        return '<div class="row s-' + esc(r.source) + '" data-id="' + esc(r.id) + '" title="' + esc(srcLabel(r.source)) + '">'
          + '<div class="row-top">'
          +   '<span class="fname" title="' + esc(r.filePath) + '">' + esc(r.fileName) + '</span>'
          +   '<span class="badges">' + riskBadge(r.risk) + reviewBadge(r.reviewState) + '</span>'
          + '</div>'
          + '<div class="row-sub">'
          +   '<span>' + esc(r.language || 'text') + '</span>'
          +   '<span class="dot">·</span><span class="lines">+' + r.linesAdded + '</span>'
          +   '<span class="dot">·</span><span>' + rel(r.timestamp) + '</span>'
          +   '<span class="conf ' + confCls(pct) + '" title="AI confidence"><span class="cdot"></span>' + pct + '%</span>'
          + '</div>'
          + '<div class="reveal">'
          +   preview
          +   '<div class="row-actions">'
          +     '<button class="act primary" data-act="insert">⤵ Insert</button>'
          +     '<button class="act" data-act="copy">⧉ Copy</button>'
          +     '<button class="act" data-act="reveal">↗ File</button>'
          +     '<button class="act del" data-act="delete">🗑</button>'
          +   '</div>'
          + '</div>'
          + '</div>';
      }

      function groupsFor(recs) {
        const map = new Map();
        for (const r of recs) {
          let key, label;
          if (group === 'file') { key = r.fileName || 'unknown'; label = key; }
          else if (group === 'source') { key = r.source; label = srcLabel(r.source); }
          else { const b = dayBucket(r.timestamp); key = b.key; label = b.label; }
          const g = map.get(key);
          if (g) g.recs.push(r); else map.set(key, { label, recs: [r] });
        }
        return [...map.entries()];
      }

      function render() {
        const q = filter.trim().toLowerCase();
        const visible = q ? RECORDS.filter(r => matches(r, q)) : RECORDS;
        $('count').textContent = q ? (visible.length + '/' + RECORDS.length) : String(RECORDS.length);

        const empty = $('empty'), list = $('list');
        if (RECORDS.length === 0) { empty.classList.remove('hidden'); list.innerHTML = ''; return; }
        empty.classList.add('hidden');

        if (group === 'none') {
          list.innerHTML = visible.map(cardHtml).join('') ||
            '<div class="empty"><div class="t">No matches</div><div class="s">Nothing matches “' + esc(filter) + '”.</div></div>';
          return;
        }

        let html = '';
        for (const [key, g] of groupsFor(visible)) {
          const isCol = collapsed.has(key);
          html += '<div class="group-head ' + (isCol ? 'collapsed' : '') + '" data-group="' + esc(key) + '">'
            + '<span class="caret">▾</span><span>' + esc(g.label) + '</span>'
            + '<span class="gcount">' + g.recs.length + '</span></div>';
          if (!isCol) { html += g.recs.map(cardHtml).join(''); }
        }
        list.innerHTML = html ||
          '<div class="empty"><div class="t">No matches</div><div class="s">Nothing matches “' + esc(filter) + '”.</div></div>';
      }

      // ── Events ────────────────────────────────────────────────────────────
      $('search').addEventListener('input', (e) => { filter = e.target.value; render(); });
      $('group').addEventListener('change', (e) => { group = e.target.value; render(); });
      $('clearAll').addEventListener('click', () => vscode.postMessage({ type: 'clearAll' }));
      Array.prototype.forEach.call(document.querySelectorAll('.tool-btn'), function (b) {
        b.addEventListener('click', function () {
          vscode.postMessage({ type: 'cmd', command: b.getAttribute('data-cmd') });
        });
      });

      $('list').addEventListener('click', (e) => {
        const head = e.target.closest('.group-head');
        if (head) {
          const k = head.getAttribute('data-group');
          if (collapsed.has(k)) collapsed.delete(k); else collapsed.add(k);
          render(); return;
        }
        const card = e.target.closest('.row');
        if (!card) return;
        const id = card.getAttribute('data-id');
        const actBtn = e.target.closest('.act');
        if (actBtn) {
          e.stopPropagation();
          vscode.postMessage({ type: actBtn.getAttribute('data-act'), id });
          return;
        }
        vscode.postMessage({ type: 'open', id });
      });

      window.addEventListener('message', (ev) => {
        const msg = ev.data;
        if (msg && msg.type === 'data') { RECORDS = msg.records || []; render(); }
      });

      vscode.postMessage({ type: 'ready' });
    })();
  </script>
</body>
</html>`;
  }
}
