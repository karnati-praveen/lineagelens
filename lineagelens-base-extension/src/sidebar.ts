import * as vscode from 'vscode';
import { CaptureRecord, ReviewState } from './store';
import { checklistFor } from './review/checklist';
import { InstructionFile } from './risk/instructionScan';

/** Human label for a capture's best-guess origin. */
function sourceLabel(source: string): string {
  return source === 'ai' ? '🤖 AI' : source === 'paste' ? '📋 Paste' : '❓ Unknown';
}

/** Human label for the review lifecycle state. */
function reviewLabel(state: ReviewState | undefined): string {
  switch (state) {
    case 'reviewed': return 'Reviewed';
    case 'needs_changes': return 'Needs changes';
    case 'rejected': return 'Rejected';
    case 'accepted': return 'Accepted';
    default: return 'Unreviewed';
  }
}

// ── Webview detail panel ──────────────────────────────────────────────────────

export function buildDetailPanel(
  panel: vscode.WebviewPanel,
  record: CaptureRecord,
  instructionFiles: InstructionFile[] = [],
): void {
  const date = new Date(record.timestamp).toLocaleString();
  const esc  = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const pct = Math.round((record.confidence ?? 0.5) * 100);
  const srcLabel = sourceLabel(record.source);
  const reviewState = record.reviewState ?? 'unreviewed';
  const reviewStatusLabel = reviewLabel(reviewState);
  const risks = record.riskSignals ?? [];
  // Tailor the review checklist to the highest-severity risk category.
  const checklistCategory = risks[0]?.category ?? 'generic';

  const langColors: Record<string, string> = {
    typescript: '#3178c6', javascript: '#f7df1e', python: '#3572a5',
    rust: '#dea584', go: '#00add8', java: '#b07219', cpp: '#f34b7d',
    csharp: '#178600', html: '#e34c26', css: '#563d7c', json: '#292929',
    yaml: '#cb171e', markdown: '#083fa1', shell: '#89e051', bash: '#89e051',
    sql: '#e38c00',
  };
  const langColor = langColors[record.language.toLowerCase()] ?? '#6b7280';

  // Embed the code + language for the client-side highlighter. JSON.stringify
  // escapes quotes/newlines; the </ guard prevents a literal "</script>" inside
  // the captured code from prematurely closing the inline script.
  const jsonString = (v: unknown) => JSON.stringify(v).replace(/</g, '\\u003c');

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
    margin-bottom: 16px;
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
  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; align-items: center; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none; border-radius: 5px; padding: 6px 12px;
    font-size: 12px; cursor: pointer; font-family: var(--vscode-font-family);
  }
  .btn:hover { background: var(--vscode-button-hoverBackground); }
  .btn.secondary {
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
  }
  .btn.secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .btn.danger { background: transparent; color: var(--vscode-errorForeground); border: 1px solid var(--vscode-errorForeground); }
  .btn.danger:hover { background: var(--vscode-errorForeground); color: var(--vscode-editor-background); }
  .reclass { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; }
  .reclass-label { font-size: 11px; color: var(--vscode-descriptionForeground); }
  .seg { display: inline-flex; border: 1px solid var(--vscode-panel-border); border-radius: 5px; overflow: hidden; }
  .seg button {
    background: transparent; color: var(--vscode-foreground);
    border: none; padding: 5px 10px; font-size: 11px; cursor: pointer;
    border-right: 1px solid var(--vscode-panel-border); font-family: var(--vscode-font-family);
  }
  .seg button:last-child { border-right: none; }
  .seg button.active { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .seg button:hover:not(.active) { background: var(--vscode-list-hoverBackground); }
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
  .code-wrap {
    background: var(--vscode-textCodeBlock-background);
    border: 1px solid var(--vscode-panel-border);
    border-radius: 8px; overflow-x: auto;
    font-family: var(--vscode-editor-font-family, monospace);
    font-size: 12px; line-height: 1.6;
  }
  .code-wrap table { border-collapse: collapse; width: 100%; }
  .code-wrap td { padding: 0; vertical-align: top; }
  .ln {
    text-align: right; padding: 0 10px 0 12px;
    color: var(--vscode-descriptionForeground); opacity: .55;
    user-select: none; white-space: nowrap;
    border-right: 2px solid rgba(34,197,94,.4);
    background: rgba(34,197,94,.06);
  }
  .lc { padding: 0 14px; white-space: pre; }
  /* lightweight highlighter token colours */
  .tk-kw  { color: #c586c0; }
  .tk-str { color: #ce9178; }
  .tk-num { color: #b5cea8; }
  .tk-com { color: #6a9955; font-style: italic; }
  .path { font-family: var(--vscode-editor-font-family, monospace); font-size: 11px; color: var(--vscode-descriptionForeground); word-break: break-all; }
  .id   { font-family: var(--vscode-editor-font-family, monospace); font-size: 10px; color: var(--vscode-descriptionForeground); opacity: .6; }
  /* ── Review section ─────────────────────────────────────────────────────── */
  .review-box {
    border: 1px solid var(--vscode-panel-border); border-radius: 8px;
    padding: 12px 14px; margin-bottom: 18px;
    background: var(--vscode-textBlockQuote-background, var(--vscode-editor-inactiveSelectionBackground));
  }
  .review-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .review-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--vscode-descriptionForeground); }
  .review-status { margin-left: auto; font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 10px; border: 1px solid var(--vscode-panel-border); }
  .review-status.s-reviewed, .review-status.s-accepted { color: #34d058; border-color: rgba(52,208,88,.4); }
  .review-status.s-needs_changes, .review-status.s-rejected { color: #f44747; border-color: rgba(244,71,71,.4); }
  .review-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .rbtn {
    background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground);
    border: 1px solid transparent; border-radius: 5px; padding: 5px 10px; font-size: 11px; cursor: pointer;
    font-family: var(--vscode-font-family);
  }
  .rbtn:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .rbtn.active { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  .review-note {
    width: 100%; min-height: 52px; resize: vertical; box-sizing: border-box;
    background: var(--vscode-input-background); color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, var(--vscode-panel-border)); border-radius: 6px;
    padding: 6px 8px; font-family: var(--vscode-font-family); font-size: 12px; margin-bottom: 8px;
  }
  .review-note:focus { outline: none; border-color: var(--vscode-focusBorder); }
  .checklist { margin-top: 6px; font-size: 12px; }
  .checklist summary { cursor: pointer; color: var(--vscode-descriptionForeground); }
  .checklist ul { margin: 8px 0 0 18px; }
  .checklist li { margin-bottom: 4px; line-height: 1.45; }
  /* ── Risk signals ───────────────────────────────────────────────────────── */
  .risk-box {
    border: 1px solid rgba(244,71,71,.35); border-radius: 8px;
    padding: 12px 14px; margin-bottom: 14px;
    background: rgba(244,71,71,.06);
  }
  .risk-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
  .risk-title { font-size: 12px; font-weight: 700; color: var(--vscode-errorForeground); }
  .risk-note { font-size: 10px; color: var(--vscode-descriptionForeground); }
  .risk-list { list-style: none; }
  .risk-list li { padding: 6px 0; border-top: 1px solid var(--vscode-panel-border); }
  .risk-list li:first-child { border-top: none; }
  .risk-pill { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 4px; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
  .risk-sev { font-size: 10px; font-weight: 700; text-transform: uppercase; margin-left: 8px; letter-spacing: .04em; }
  .sev-high .risk-sev { color: #f44747; }
  .sev-medium .risk-sev { color: #f5a623; }
  .sev-low .risk-sev { color: var(--vscode-descriptionForeground); }
  .risk-msg { font-size: 11.5px; color: var(--vscode-foreground); margin-top: 3px; line-height: 1.45; }
  /* ── AI instruction influence ───────────────────────────────────────────── */
  .instr-box {
    border: 1px solid var(--vscode-panel-border); border-radius: 8px;
    padding: 12px 14px; margin-bottom: 18px;
    background: var(--vscode-textBlockQuote-background, var(--vscode-editor-inactiveSelectionBackground));
  }
  .instr-head { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--vscode-descriptionForeground); margin-bottom: 6px; }
  .instr-note { font-size: 11.5px; color: var(--vscode-foreground); margin-bottom: 8px; }
  .instr-list { list-style: none; }
  .instr-list li { display: flex; align-items: baseline; gap: 8px; padding: 3px 0; }
  .instr-tool { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 4px; background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); white-space: nowrap; }
  .instr-path { font-family: var(--vscode-editor-font-family, monospace); font-size: 11px; color: var(--vscode-descriptionForeground); word-break: break-all; }
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
  <div class="actions">
    <button class="btn" id="insertBtn" type="button">⤵ Insert at cursor</button>
    <button class="btn secondary" id="copyBtn" type="button">Copy</button>
    <button class="btn secondary" id="revealBtn" type="button">↗ Reveal in file</button>
    <button class="btn secondary" id="recallBtn" type="button">⟲ Recall similar</button>
    <button class="btn danger" id="deleteBtn" type="button">Delete</button>
    <span class="reclass">
      <span class="reclass-label">Reclassify:</span>
      <span class="seg">
        <button data-src="ai"      class="${record.source === 'ai' ? 'active' : ''}" type="button">🤖 AI</button>
        <button data-src="paste"   class="${record.source === 'paste' ? 'active' : ''}" type="button">📋 Paste</button>
        <button data-src="unknown" class="${record.source === 'unknown' ? 'active' : ''}" type="button">❓ Unknown</button>
      </span>
    </span>
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
    <div class="card">
      <div class="card-label">Source</div>
      <div class="card-value">${srcLabel}</div>
    </div>
    <div class="card">
      <div class="card-label">AI Confidence</div>
      <div class="card-value">${pct}%</div>
    </div>
    ${record.workspaceFolder ? `<div class="card" style="grid-column:1/-1"><div class="card-label">Workspace</div><div class="card-value">${esc(record.workspaceFolder)}</div></div>` : ''}
  </div>
  ${risks.length ? `<div class="risk-box">
    <div class="risk-head"><span class="risk-title">⚠ Local risk signals</span><span class="risk-note">heuristic — not a security scan</span></div>
    <ul class="risk-list">
      ${risks.map(s => `<li class="sev-${esc(s.severity)}"><span class="risk-pill">${esc(s.label)}</span><span class="risk-sev">${esc(s.severity)}</span><div class="risk-msg">${esc(s.message)}</div></li>`).join('')}
    </ul>
  </div>` : ''}
  <div class="review-box">
    <div class="review-head">
      <span class="review-title">Review</span>
      <span class="review-status s-${esc(reviewState)}">${esc(reviewStatusLabel)}</span>
    </div>
    <div class="review-actions">
      <button class="rbtn ${reviewState === 'reviewed' ? 'active' : ''}" data-review="reviewed" type="button">✓ Reviewed</button>
      <button class="rbtn ${reviewState === 'needs_changes' ? 'active' : ''}" data-review="needs_changes" type="button">✎ Needs changes</button>
      <button class="rbtn ${reviewState === 'rejected' ? 'active' : ''}" data-review="rejected" type="button">✕ Rejected</button>
      <button class="rbtn ${reviewState === 'unreviewed' ? 'active' : ''}" data-review="unreviewed" type="button">↺ Reopen</button>
    </div>
    <textarea id="reviewNote" class="review-note" placeholder="Add a review note (optional)…">${esc(record.reviewNote ?? '')}</textarea>
    <button class="btn secondary" id="saveNote" type="button">Save note</button>
    <details class="checklist">
      <summary>Review checklist${checklistCategory !== 'generic' ? ` · ${esc(checklistCategory)}` : ''}</summary>
      <ul>${checklistFor(checklistCategory).map(item => `<li>${esc(item)}</li>`).join('')}</ul>
    </details>
  </div>
  ${instructionFiles.length ? `<div class="instr-box">
    <div class="instr-head">AI instruction influence</div>
    <div class="instr-note">This change may have been shaped by project AI instruction files:</div>
    <ul class="instr-list">
      ${instructionFiles.map(f => `<li><span class="instr-tool">${esc(f.tool)}</span><span class="instr-path">${esc(f.path)}</span></li>`).join('')}
    </ul>
  </div>` : ''}
  <div class="code-header">
    <span class="code-label">Inserted Code</span>
  </div>
  <div class="code-wrap"><table id="codeTable"><tbody></tbody></table></div>
  <div style="margin-top:14px"><span class="id">ID: ${esc(record.id)}</span></div>
  <script>
    (function() {
      var vscode = acquireVsCodeApi();
      var CODE = ${jsonString(record.insertedCode)};
      var LANG = ${jsonString(record.language)};

      // ── Minimal, single-pass tokenizer ──────────────────────────────────────
      // One combined regex with ordered alternatives avoids nested-span bugs:
      // each character belongs to exactly one matched token.
      var KEYWORDS = ('const let var function return if else for while do switch case break ' +
        'continue class extends new this super import export from default async await yield ' +
        'try catch finally throw typeof instanceof in of void delete null undefined true false ' +
        'def lambda pass elif except with as raise global nonlocal print None True False and or not ' +
        'public private protected static final void int string bool float double struct enum interface ' +
        'fn let mut impl trait pub use mod match where type package func go defer chan select').split(' ');
      var KW = {}; KEYWORDS.forEach(function(k){ KW[k] = true; });

      var TOKEN = /(\\/\\/[^\\n]*|#[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)|("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`(?:[^\`\\\\]|\\\\.)*\`)|(\\b\\d[\\d_.]*\\b)|([A-Za-z_$][A-Za-z0-9_$]*)/g;

      function highlightLine(line) {
        var out = '', last = 0, m;
        TOKEN.lastIndex = 0;
        while ((m = TOKEN.exec(line)) !== null) {
          out += escapeText(line.slice(last, m.index));
          if (m[1])      { out += '<span class="tk-com">' + escapeText(m[1]) + '</span>'; }
          else if (m[2]) { out += '<span class="tk-str">' + escapeText(m[2]) + '</span>'; }
          else if (m[3]) { out += '<span class="tk-num">' + escapeText(m[3]) + '</span>'; }
          else if (m[4]) {
            out += KW[m[4]]
              ? '<span class="tk-kw">' + escapeText(m[4]) + '</span>'
              : escapeText(m[4]);
          }
          last = m.index + m[0].length;
        }
        out += escapeText(line.slice(last));
        return out || '&nbsp;';
      }

      function escapeText(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      }

      var tbody = document.querySelector('#codeTable tbody');
      CODE.split('\\n').forEach(function(line, i) {
        var tr = document.createElement('tr');
        var num = document.createElement('td'); num.className = 'ln'; num.textContent = String(i + 1);
        var code = document.createElement('td'); code.className = 'lc'; code.innerHTML = highlightLine(line);
        tr.appendChild(num); tr.appendChild(code); tbody.appendChild(tr);
      });

      function on(id, fn) { var el = document.getElementById(id); if (el) { el.addEventListener('click', fn); } }
      on('insertBtn', function() { vscode.postMessage({ type: 'insert' }); });
      on('revealBtn', function() { vscode.postMessage({ type: 'reveal' }); });
      on('recallBtn', function() { vscode.postMessage({ type: 'recall' }); });
      on('deleteBtn', function() { vscode.postMessage({ type: 'delete' }); });
      on('copyBtn', function() {
        var btn = document.getElementById('copyBtn');
        if (navigator.clipboard) {
          navigator.clipboard.writeText(CODE).then(function() {
            btn.textContent = 'Copied!'; setTimeout(function(){ btn.textContent = 'Copy'; }, 1500);
          }).catch(function() { vscode.postMessage({ type: 'copy' }); });
        } else { vscode.postMessage({ type: 'copy' }); }
      });
      Array.prototype.forEach.call(document.querySelectorAll('.seg button'), function(b) {
        b.addEventListener('click', function() {
          vscode.postMessage({ type: 'reclassify', source: b.getAttribute('data-src') });
        });
      });
      Array.prototype.forEach.call(document.querySelectorAll('.rbtn'), function(b) {
        b.addEventListener('click', function() {
          var note = document.getElementById('reviewNote');
          vscode.postMessage({ type: 'review', state: b.getAttribute('data-review'), note: note ? note.value : '' });
        });
      });
      on('saveNote', function() {
        var note = document.getElementById('reviewNote');
        vscode.postMessage({ type: 'review', note: note ? note.value : '' });
      });
    })();
  </script>
</body>
</html>`;
}
